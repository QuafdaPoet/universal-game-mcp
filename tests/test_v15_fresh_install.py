"""
Tests for Test Scenario H: Fresh Installation & Unlearned Game Exploration
Validates that:
- Clean installation initializes SQLite database with WAL mode and schema migrations from scratch.
- Memory starts completely clean without synthetic artifacts or fixtures pre-baked into real knowledge.
- Unknown / unlearned game scenes are safely classified as 'unknown' without crashes.
- Skill pack loader functions safely.
- NavigationGraph functions gracefully on an empty database.
"""

import sqlite3
import pytest
import numpy as np
from pathlib import Path
from unittest.mock import MagicMock

from ugm.memory.database import DatabaseManager
from ugm.memory.storage import PersistentKnowledgeStorage
from ugm.navigation.recognizer import UIStateRecognizer
from ugm.navigation.graph import NavigationGraph
from ugm.skills.loader import GameSkillPackLoader, ManifestValidationError
from ugm.core.ocr import BaseOCREngine
from ugm.core.icon import IconDetector
from ugm.core.types import OCRResult


def test_fresh_database_initialization(tmp_path):
    """Test that a fresh SQLite database initializes schema v1 and WAL mode."""
    db_path = tmp_path / "fresh_ugm.db"
    assert not db_path.exists()

    db_mgr = DatabaseManager(db_path=db_path)
    storage = PersistentKnowledgeStorage(db_manager=db_mgr)

    assert db_path.exists()

    # Verify WAL mode
    with db_mgr.get_connection() as conn:
        journal_mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        assert journal_mode.lower() == "wal"

        # Verify schema tables exist
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()
        table_names = {row["name"] for row in tables}
        expected_tables = {
            "schema_version", "games", "ui_states",
            "selectors_memory", "action_history",
            "navigation_transitions", "learned_skills"
        }
        for expected in expected_tables:
            assert expected in table_names, f"Expected table {expected} in fresh database"

    # Verify initial stats are empty
    stats = storage.get_statistics("hoi4")
    assert stats["ui_states_count"] == 0
    assert stats["transitions_count"] == 0
    assert stats["total_learned_skills"] == 0
    assert stats["action_history_count"] == 0


def test_unlearned_game_exploration_graceful_classification(tmp_path):
    """An unknown game screen with no pre-existing memory classifies gracefully as 'unknown'."""
    db_mgr = DatabaseManager(db_path=tmp_path / "unknown_game.db")
    storage = PersistentKnowledgeStorage(db_manager=db_mgr)

    ocr_mock = MagicMock(spec=BaseOCREngine)
    ocr_result = OCRResult(items=[], full_text="", engine="mock", latency_ms=1.0)
    ocr_mock.read_text.return_value = ocr_result
    icon_mock = MagicMock(spec=IconDetector)
    icon_mock.locate_icon.return_value = []

    recognizer = UIStateRecognizer(
        ocr_engine=ocr_mock,
        icon_detector=icon_mock,
        storage=storage
    )

    # Empty frame (e.g. random noise or unlearned game menu)
    rng = np.random.RandomState(42)
    unlearned_frame = rng.randint(0, 255, (720, 1280, 3), dtype=np.uint8)

    state_result = recognizer.recognize_state(
        frame=unlearned_frame,
        game_id="new_unsupported_game"
    )

    assert state_result is not None
    assert state_result.state_name == "unknown"
    assert state_result.is_known is False
    assert state_result.confidence <= 0.65
    assert "evidence" in state_result.to_dict()


def test_navigation_graph_empty_memory(tmp_path):
    """NavigationGraph handles empty database gracefully without raising errors."""
    db_mgr = DatabaseManager(db_path=tmp_path / "empty_nav.db")
    storage = PersistentKnowledgeStorage(db_manager=db_mgr)
    graph = NavigationGraph(storage=storage, game_id="hoi4")

    # No nodes or edges exist yet
    plan = graph.plan_route("main_map", "production")
    assert plan.found is False
    assert len(plan.edges) == 0
    assert plan.total_cost == float("inf")


def test_pack_loader_on_fresh_environment(tmp_path):
    """GameSkillPackLoader raises clean error on empty dir and loads valid pack."""
    empty_dir = tmp_path / "empty_packs"
    empty_dir.mkdir()
    with pytest.raises(ManifestValidationError):
        GameSkillPackLoader.load_pack(str(empty_dir))

    # Real repo packs directory
    pack = GameSkillPackLoader.load_pack("game_skills/hoi4")
    assert pack.game_id == "hoi4"
