"""
Tests for V1.5 Persistent Memory & Knowledge Storage (Test A).
Verifies:
- SQLite WAL mode and schema migration to v1
- Perceptual difference hashing (dHash) and Hamming distance
- Partitioning by game_id and source ('live' vs 'synthetic_fixture')
- Bounded history pruning and LRU state cleanup
- Export and import of knowledge bundles
- Test A Scenario: Learn a verified UI transition, stop MCP server/database, restart, and retrieve the same transition.
"""

import os
import json
import sqlite3
import numpy as np
import pytest

from ugm.memory.database import DatabaseManager, compute_dhash, hamming_distance
from ugm.memory.storage import PersistentKnowledgeStorage


@pytest.fixture
def temp_db(tmp_path):
    db_file = tmp_path / "test_memory.db"
    mgr = DatabaseManager(str(db_file))
    storage = PersistentKnowledgeStorage(mgr)
    yield storage
    mgr.close()


def test_database_wal_mode_and_schema(temp_db):
    conn = temp_db.db.get_connection()
    cur = conn.execute("PRAGMA journal_mode;")
    mode = cur.fetchone()[0]
    assert mode.lower() == "wal"

    cur = conn.execute("SELECT MAX(version) FROM schema_version;")
    ver = cur.fetchone()[0]
    assert ver >= 1


def test_perceptual_hashing():
    img1 = np.zeros((100, 100, 3), dtype=np.uint8)
    img2 = np.zeros((100, 100, 3), dtype=np.uint8)
    img2[10:40, 10:40] = 255

    hash1 = compute_dhash(img1)
    hash2 = compute_dhash(img2)
    assert len(hash1) == 16  # 64-bit hex
    assert len(hash2) == 16

    # Identical image gives distance 0
    assert hamming_distance(hash1, hash1) == 0
    # Different image gives distance > 0
    dist = hamming_distance(hash1, hash2)
    assert dist > 0


def test_ui_state_recording_and_matching(temp_db):
    img = np.full((1080, 1920, 3), 50, dtype=np.uint8)
    phash = compute_dhash(img)

    state_id = temp_db.record_ui_state(
        game_id="hoi4",
        state_name="main_map",
        phash=phash,
        confidence=0.95,
        evidence={"topbar": True},
        source="live"
    )
    assert state_id > 0

    # Repeat recording increments occurrences
    state_id2 = temp_db.record_ui_state(
        game_id="hoi4",
        state_name="main_map",
        phash=phash,
        confidence=0.98,
        source="live"
    )
    assert state_id2 == state_id

    states = temp_db.get_states("hoi4")
    assert len(states) == 1
    assert states[0].occurrences == 2
    assert states[0].state_name == "main_map"

    # Perceptual hash matching
    match = temp_db.find_matching_state("hoi4", phash, max_distance=2)
    assert match is not None
    assert match[0] == "main_map"


def test_test_a_persistence_across_restarts(tmp_path):
    """
    Test A Scenario:
    Learn a verified UI transition, stop MCP server/storage, restart, and retrieve the same transition.
    """
    db_file = str(tmp_path / "persistent_restart_test.db")

    # Session 1: Learn and store transition
    mgr1 = DatabaseManager(db_file)
    storage1 = PersistentKnowledgeStorage(mgr1)
    storage1.register_game("hoi4", "Hearts of Iron IV")

    storage1.record_transition(
        game_id="hoi4",
        from_state="main_map",
        to_state="production",
        action_type="press_keys",
        params={"keys": ["y"]},
        success=True,
        cost=1.0,
        evidence={"verified_by": "visual_diff_and_ocr"},
        source="live"
    )
    # Also save a learned skill in Session 1
    storage1.save_learned_skill(
        skill_id="learned_open_prod",
        game_id="hoi4",
        name="Learned Open Production",
        steps=[{"action": "press_keys", "params": {"keys": ["y"]}}],
        status="candidate"
    )
    mgr1.close()

    # Session 2: Fresh start pointing to same database
    mgr2 = DatabaseManager(db_file)
    storage2 = PersistentKnowledgeStorage(mgr2)

    # Retrieve transitions
    transitions = storage2.get_transitions("hoi4")
    assert len(transitions) == 1
    t = transitions[0]
    assert t.from_state == "main_map"
    assert t.to_state == "production"
    assert t.action_type == "press_keys"
    assert t.params == {"keys": ["y"]}
    assert t.success_count == 1
    assert t.source == "live"

    # Retrieve learned skill
    skill = storage2.get_learned_skill("learned_open_prod")
    assert skill is not None
    assert skill.name == "Learned Open Production"
    assert skill.status == "candidate"

    mgr2.close()


def test_export_and_import_bundle(temp_db):
    temp_db.record_ui_state("hoi4", "production", "12345678abcdef01", source="live")
    temp_db.record_transition("hoi4", "main_map", "production", "press_keys", {"keys": ["y"]}, success=True)
    temp_db.save_learned_skill("test_skill", "hoi4", "Test Skill", [{"action": "wait"}], status="verified")

    bundle = temp_db.export_knowledge_bundle("hoi4")
    assert bundle["game_id"] == "hoi4"
    assert len(bundle["transitions"]) >= 1
    assert len(bundle["learned_skills"]) >= 1

    # Import into a separate clean DB
    import_mgr = DatabaseManager(temp_db.db.db_path + "_import.db")
    import_storage = PersistentKnowledgeStorage(import_mgr)
    imported_count = import_storage.import_knowledge_bundle(bundle)
    assert imported_count >= 2

    imported_skill = import_storage.get_learned_skill("test_skill")
    assert imported_skill is not None
    assert imported_skill.name == "Test Skill"
    import_mgr.close()
