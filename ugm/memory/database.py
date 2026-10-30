"""
Universal Game MCP (UGM) - SQLite Persistent Memory & Schema Migrations
Provides thread-safe local knowledge storage using SQLite with WAL mode,
automatic schema migrations, bounded history pruning, and perceptual hashing.
"""

from __future__ import annotations
import os
import sqlite3
import threading
import time
import json
import logging
from typing import Optional, List, Dict, Any, Tuple
import numpy as np
import cv2

logger = logging.getLogger("ugm.memory.database")

CURRENT_SCHEMA_VERSION = 2


def compute_dhash(image: np.ndarray, hash_size: int = 8) -> str:
    """
    Computes perceptual difference hash (dHash) for an image frame.
    Fast, robust to subtle color and brightness changes, CPU-efficient.
    Returns hexadecimal hash string.
    """
    if image is None or image.size == 0:
        return "0" * (hash_size * hash_size // 4)

    # 1. Resize to (hash_size + 1, hash_size)
    if len(image.shape) == 3:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    else:
        gray = image

    resized = cv2.resize(gray, (hash_size + 1, hash_size), interpolation=cv2.INTER_AREA)

    # 2. Compare adjacent pixels: compute difference matrix
    diff = resized[:, 1:] > resized[:, :-1]

    # 3. Convert boolean array to hex string
    bit_string = "".join(["1" if b else "0" for b in diff.flatten()])
    hex_str = f"{int(bit_string, 2):0{hash_size * hash_size // 4}x}"
    return hex_str


def hamming_distance(hash1: str, hash2: str) -> int:
    """
    Calculates Hamming distance between two hex hash strings.
    A lower distance indicates greater visual similarity (0 = identical).
    """
    if not hash1 or not hash2 or len(hash1) != len(hash2):
        return 999
    try:
        val1 = int(hash1, 16)
        val2 = int(hash2, 16)
        xor_val = val1 ^ val2
        return bin(xor_val).count("1")
    except ValueError:
        return 999


class DatabaseManager:
    """
    Manages thread-safe SQLite connection pool and schema migrations.
    """

    def __init__(self, db_path: str = "data/memory/ugm_memory.db"):
        self.db_path = os.path.abspath(db_path)
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._local = threading.local()
        self._init_database()

    def get_connection(self) -> sqlite3.Connection:
        """Returns a thread-local SQLite connection configured with WAL mode."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(self.db_path, timeout=10.0)
            conn.row_factory = sqlite3.Row
            # Enable WAL mode and foreign keys
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.execute("PRAGMA foreign_keys=ON;")
            conn.execute("PRAGMA busy_timeout=5000;")
            self._local.conn = conn
        return self._local.conn

    def close(self) -> None:
        """Closes thread-local database connection."""
        if hasattr(self._local, "conn") and self._local.conn is not None:
            try:
                self._local.conn.close()
            except Exception:
                pass
            self._local.conn = None

    def _init_database(self) -> None:
        """Runs pending schema migrations."""
        conn = self.get_connection()
        with conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER PRIMARY KEY,
                    applied_at REAL NOT NULL
                )
            """)
            cur = conn.execute("SELECT MAX(version) FROM schema_version")
            row = cur.fetchone()
            current_ver = row[0] if row and row[0] is not None else 0

            if current_ver < CURRENT_SCHEMA_VERSION:
                self._migrate(conn, from_ver=current_ver, to_ver=CURRENT_SCHEMA_VERSION)

    def _migrate(self, conn: sqlite3.Connection, from_ver: int, to_ver: int) -> None:
        """Applies sequential schema migrations."""
        logger.info("Migrating UGM database schema from v%d to v%d...", from_ver, to_ver)

        if from_ver < 1:
            # Migration v1: Core V1.5 knowledge tables
            conn.executescript("""
                -- Registered games
                CREATE TABLE IF NOT EXISTS games (
                    game_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    version TEXT DEFAULT '1.0.0',
                    base_res_w INTEGER DEFAULT 1920,
                    base_res_h INTEGER DEFAULT 1080,
                    ui_config TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );

                -- Observed UI States
                CREATE TABLE IF NOT EXISTS ui_states (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_id TEXT NOT NULL,
                    state_name TEXT NOT NULL,
                    phash TEXT NOT NULL,
                    confidence REAL NOT NULL DEFAULT 1.0,
                    evidence TEXT,
                    metadata TEXT,
                    source TEXT NOT NULL DEFAULT 'live',
                    first_seen REAL NOT NULL,
                    last_seen REAL NOT NULL,
                    occurrences INTEGER NOT NULL DEFAULT 1,
                    UNIQUE(game_id, state_name, phash)
                );
                CREATE INDEX IF NOT EXISTS idx_ui_states_lookup 
                ON ui_states (game_id, state_name);

                -- Selector experience memory
                CREATE TABLE IF NOT EXISTS selectors_memory (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_id TEXT NOT NULL,
                    selector_id TEXT NOT NULL,
                    state_name TEXT NOT NULL,
                    selector_data TEXT,
                    confidence REAL NOT NULL DEFAULT 1.0,
                    success_count INTEGER NOT NULL DEFAULT 0,
                    fail_count INTEGER NOT NULL DEFAULT 0,
                    last_verified REAL NOT NULL,
                    source TEXT NOT NULL DEFAULT 'live',
                    UNIQUE(game_id, selector_id, state_name)
                );

                -- Action execution history
                CREATE TABLE IF NOT EXISTS action_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_id TEXT NOT NULL,
                    from_state TEXT,
                    to_state TEXT,
                    action_name TEXT NOT NULL,
                    params TEXT,
                    success INTEGER NOT NULL,
                    reason TEXT,
                    duration_ms REAL DEFAULT 0.0,
                    source TEXT NOT NULL DEFAULT 'live',
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_action_hist_game
                ON action_history (game_id, created_at);

                -- Navigation graph transitions (Edges)
                CREATE TABLE IF NOT EXISTS navigation_transitions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_id TEXT NOT NULL,
                    from_state TEXT NOT NULL,
                    to_state TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    params TEXT NOT NULL,
                    cost REAL NOT NULL DEFAULT 1.0,
                    success_count INTEGER NOT NULL DEFAULT 1,
                    fail_count INTEGER NOT NULL DEFAULT 0,
                    confidence REAL NOT NULL DEFAULT 1.0,
                    evidence TEXT,
                    source TEXT NOT NULL DEFAULT 'live',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    UNIQUE(game_id, from_state, to_state, action_type, params)
                );
                CREATE INDEX IF NOT EXISTS idx_nav_trans_from
                ON navigation_transitions (game_id, from_state);

                -- Procedurally learned and verified skills
                CREATE TABLE IF NOT EXISTS learned_skills (
                    skill_id TEXT PRIMARY KEY,
                    game_id TEXT NOT NULL,
                    version TEXT NOT NULL DEFAULT '1.0.0',
                    name TEXT NOT NULL,
                    description TEXT,
                    preconditions TEXT,
                    steps TEXT NOT NULL,
                    post_verification TEXT,
                    status TEXT NOT NULL DEFAULT 'candidate', -- 'candidate', 'verified', 'rejected'
                    validation_count INTEGER NOT NULL DEFAULT 0,
                    success_count INTEGER NOT NULL DEFAULT 0,
                    fail_count INTEGER NOT NULL DEFAULT 0,
                    confidence REAL NOT NULL DEFAULT 0.5,
                    source TEXT NOT NULL DEFAULT 'live',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_learned_skills_game
                ON learned_skills (game_id, status);

                INSERT INTO schema_version (version, applied_at) VALUES (1, unixepoch('subsec'));
            """)

        if from_ver < 2:
            # Migration v2: Checkpoints, cross-game concepts, and 4-state knowledge lifecycle
            def _add_column_if_missing(table: str, col_name: str, col_type: str):
                cur = conn.execute(f"PRAGMA table_info({table});")
                cols = [r[1] for r in cur.fetchall()]
                if col_name not in cols:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type};")

            _add_column_if_missing("navigation_transitions", "status", "TEXT NOT NULL DEFAULT 'candidate'")
            _add_column_if_missing("navigation_transitions", "consecutive_failures", "INTEGER NOT NULL DEFAULT 0")
            _add_column_if_missing("selectors_memory", "status", "TEXT NOT NULL DEFAULT 'candidate'")
            _add_column_if_missing("selectors_memory", "consecutive_failures", "INTEGER NOT NULL DEFAULT 0")
            _add_column_if_missing("learned_skills", "consecutive_failures", "INTEGER NOT NULL DEFAULT 0")
            _add_column_if_missing("learned_skills", "optimization_notes", "TEXT")

            conn.executescript("""
                -- Knowledge point-in-time checkpoints for rollback
                CREATE TABLE IF NOT EXISTS checkpoints (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_id TEXT NOT NULL,
                    label TEXT NOT NULL,
                    description TEXT,
                    snapshot_data TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_checkpoints_game
                ON checkpoints (game_id, created_at);

                -- Universal cross-game concepts & heuristics library
                CREATE TABLE IF NOT EXISTS cross_game_concepts (
                    concept_id TEXT PRIMARY KEY,
                    category TEXT NOT NULL,
                    name TEXT NOT NULL,
                    description TEXT,
                    default_action TEXT NOT NULL,
                    default_params TEXT NOT NULL,
                    typical_effects TEXT,
                    created_at REAL NOT NULL
                );

                -- Insert standard universal concept heuristics
                INSERT OR IGNORE INTO cross_game_concepts (concept_id, category, name, description, default_action, default_params, typical_effects, created_at)
                VALUES 
                    ('pause_toggle', 'controls', 'Toggle Pause/Unpause', 'Standard hotkey or button to pause/unpause simulation', 'press_keys', '{"keys": ["space"]}', '{"time_flow": "toggle"}', unixepoch('subsec')),
                    ('cancel_or_back', 'navigation', 'Cancel or Close Menu', 'Dismiss current dialog, drawer, or modal and return to viewport', 'press_keys', '{"keys": ["escape"]}', '{"modal": "closed"}', unixepoch('subsec')),
                    ('cancel_dialog', 'dialogs', 'Cancel / Dismiss Dialog', 'Dismiss active dialog or return', 'press_keys', '{"keys": ["escape"]}', '{"dialog": "dismissed"}', unixepoch('subsec')),
                    ('confirm_dialog', 'dialogs', 'Confirm Selection / Submit', 'Confirm current modal selection or accept prompt', 'press_keys', '{"keys": ["enter"]}', '{"dialog": "submitted"}', unixepoch('subsec')),
                    ('game_speed', 'controls', 'Adjust Game Speed', 'Keys to control simulation speed', 'press_keys', '{"keys": ["1", "2", "3", "4", "5"]}', '{"speed": "changed"}', unixepoch('subsec')),
                    ('speed_increase', 'controls', 'Increase Game Speed', 'Numpad plus or number key to accelerate game tick', 'press_keys', '{"keys": ["num+"]}', '{"speed": "faster"}', unixepoch('subsec')),
                    ('speed_decrease', 'controls', 'Decrease Game Speed', 'Numpad minus or number key to decelerate game tick', 'press_keys', '{"keys": ["num-"]}', '{"speed": "slower"}', unixepoch('subsec')),
                    ('pan_map_up', 'navigation', 'Pan Map North', 'Pan camera or viewport up', 'press_keys', '{"keys": ["w"]}', '{"camera": "north"}', unixepoch('subsec')),
                    ('pan_map_down', 'navigation', 'Pan Map South', 'Pan camera or viewport down', 'press_keys', '{"keys": ["s"]}', '{"camera": "south"}', unixepoch('subsec')),
                    ('pan_map_left', 'navigation', 'Pan Map West', 'Pan camera or viewport left', 'press_keys', '{"keys": ["a"]}', '{"camera": "west"}', unixepoch('subsec')),
                    ('pan_map_right', 'navigation', 'Pan Map East', 'Pan camera or viewport right', 'press_keys', '{"keys": ["d"]}', '{"camera": "east"}', unixepoch('subsec'));

                INSERT INTO schema_version (version, applied_at) VALUES (2, unixepoch('subsec'));
            """)

        logger.info("Database schema migration to v%d complete.", to_ver)
