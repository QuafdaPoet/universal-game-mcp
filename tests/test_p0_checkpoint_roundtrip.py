"""Checkpoints are transactional, complete, and bound to their game and evidence domain."""

import json
import sqlite3

import pytest

from ugm.memory.database import DatabaseManager
from ugm.memory.storage import PersistentKnowledgeStorage


TABLES = ("ui_states", "navigation_transitions", "learned_skills", "selectors_memory")
LEGACY_KEY = {"navigation_transitions": "transitions", "selectors_memory": "selectors"}


@pytest.fixture
def storage(tmp_path):
    db = DatabaseManager(str(tmp_path / "checkpoint.db"))
    try:
        yield PersistentKnowledgeStorage(db)
    finally:
        db.close()


def _dump(storage, game_id):
    conn = storage.db.get_connection()
    return {
        table: [dict(row) for row in conn.execute(
            f"SELECT * FROM {table} WHERE game_id = ? ORDER BY rowid", (game_id,)
        )]
        for table in TABLES
    }


def _seed(storage, game_id):
    storage.record_ui_state(game_id, "map", "abcdef", evidence={"map": True}, source="live")
    storage.record_ui_state(game_id, "map", "abcdef", confidence=0.6)
    storage.record_transition(game_id, "map", "panel", "press_keys", {"keys": ["a"], "hold": 2})
    storage.record_transition(game_id, "map", "blocked", "press_keys", {"keys": ["q"]}, status="rejected")
    storage.save_learned_skill("pause", game_id, "Pause", [{"action": "press_key"}], source="manual")
    storage.update_skill_stats("pause", False, game_id=game_id)
    storage.save_knowledge(game_id, "selector", {"type": "text", "value": "Exit"})

    conn = storage.db.get_connection()
    with conn:
        conn.execute(
            "UPDATE selectors_memory SET success_count=7, fail_count=3, last_verified=12.3, "
            "source='observed', status='suspect', consecutive_failures=2 WHERE game_id=?", (game_id,),
        )
        conn.execute(
            "UPDATE navigation_transitions SET created_at=13.5, updated_at=14.5, evidence=NULL, source='observed' "
            "WHERE game_id=?", (game_id,),
        )
        conn.execute(
            "UPDATE learned_skills SET created_at=15.5, updated_at=16.5, version='1.2.0', "
            "optimization_notes='Improved evidence' WHERE game_id=?", (game_id,),
        )


def _load_snapshot(storage, checkpoint_id):
    conn = storage.db.get_connection()
    row = conn.execute("SELECT snapshot_data FROM checkpoints WHERE id=?", (checkpoint_id,)).fetchone()
    return json.loads(row[0])


def _store_snapshot(storage, checkpoint_id, snapshot):
    conn = storage.db.get_connection()
    with conn:
        conn.execute("UPDATE checkpoints SET snapshot_data=? WHERE id=?", (
            snapshot if isinstance(snapshot, str) else json.dumps(snapshot), checkpoint_id,
        ))


def _snapshot_rows(snapshot, table):
    if "tables" in snapshot:
        return snapshot["tables"][table]
    return snapshot[LEGACY_KEY.get(table, table)]


def test_checkpoint_restores_exact_rows_including_rejected_transition(storage):
    _seed(storage, "first")
    _seed(storage, "second")
    first = _dump(storage, "first")
    second = _dump(storage, "second")
    checkpoint_id = storage.create_checkpoint("first", "before changes")

    storage.delete_memory(game_id="first")
    storage.record_ui_state("first", "different", "000000")
    storage.record_action_history("first", "audit", {}, True)

    assert storage.rollback_checkpoint("first", checkpoint_id) is True
    assert _dump(storage, "first") == first
    assert _dump(storage, "second") == second
    assert storage.get_transitions("first", status="rejected")[0].to_state == "blocked"
    # Audit history is not part of the rollback scope.
    assert storage.get_action_history("first")[0]["action_name"] == "audit"


@pytest.mark.parametrize("operation, read_table", [
    ("create", "NAVIGATION_TRANSITIONS"), ("restore", "CHECKPOINTS"),
])
def test_checkpoint_read_and_write_use_one_write_transaction(storage, operation, read_table):
    _seed(storage, "first")
    checkpoint_id = storage.create_checkpoint("first", "snapshot") if operation == "restore" else None
    conn = storage.db.get_connection()
    writer = sqlite3.connect(storage.db.db_path, timeout=0)
    write_attempt = []

    def try_write_during_snapshot(statement):
        if "SELECT" not in statement.upper() or read_table not in statement.upper() or write_attempt:
            return
        try:
            with writer:
                writer.execute("UPDATE ui_states SET state_name='interleaved' WHERE game_id='first'")
        except sqlite3.OperationalError as exc:
            write_attempt.append(str(exc))
        else:
            write_attempt.append("write succeeded")

    conn.set_trace_callback(try_write_during_snapshot)
    try:
        if operation == "create":
            storage.create_checkpoint("first", "snapshot")
        else:
            assert storage.rollback_checkpoint("first", checkpoint_id)
    finally:
        conn.set_trace_callback(None)
        writer.close()

    assert len(write_attempt) == 1
    assert "locked" in write_attempt[0]


@pytest.mark.parametrize("damage", [
    "legacy", "invalid_json", "unsupported_version", "missing_table", "duplicate_row", "wrong_row_game",
    "wrong_snapshot_game", "wrong_evidence_namespace", "cross_game_surrogate_id", "bad_raw_json",
    "invalid_column_type", "duplicate_json_key",
])
def test_invalid_checkpoint_leaves_all_current_rows_intact(storage, damage):
    _seed(storage, "first")
    _seed(storage, "second")
    checkpoint_id = storage.create_checkpoint("first", "corrupt this test checkpoint")
    storage.record_ui_state("first", "later", "123456")
    first = _dump(storage, "first")
    second = _dump(storage, "second")
    snapshot = _load_snapshot(storage, checkpoint_id)
    if damage == "legacy":
        snapshot = {
            "game_id": "first", "timestamp": 1.0, "label": "legacy",
            "ui_states": [], "transitions": [], "selectors": [], "learned_skills": [],
        }
    elif damage == "invalid_json":
        snapshot = '{"invalid":'
    elif damage == "unsupported_version":
        snapshot["format_version"] = 999
    elif damage == "missing_table":
        snapshot.get("tables", snapshot).pop("learned_skills")
    elif damage == "duplicate_row":
        rows = _snapshot_rows(snapshot, "learned_skills")
        rows.append(rows[0].copy())
    elif damage == "wrong_row_game":
        _snapshot_rows(snapshot, "ui_states")[0]["game_id"] = "second"
    elif damage == "wrong_snapshot_game":
        snapshot["game_id"] = "second"
    elif damage == "wrong_evidence_namespace":
        snapshot["simulation"] = True
    elif damage == "cross_game_surrogate_id":
        _snapshot_rows(snapshot, "ui_states")[0]["id"] = second["ui_states"][0]["id"]
    elif damage == "bad_raw_json":
        _snapshot_rows(snapshot, "selectors_memory")[0]["selector_data"] = '{"incomplete":'
    elif damage == "invalid_column_type":
        _snapshot_rows(snapshot, "ui_states")[0]["occurrences"] = "1"
    elif damage == "duplicate_json_key":
        snapshot = '{"game_id":"second",' + json.dumps(snapshot)[1:]
    _store_snapshot(storage, checkpoint_id, snapshot)

    assert storage.rollback_checkpoint("second", checkpoint_id) is False
    assert storage.rollback_checkpoint("first", checkpoint_id) is False
    assert _dump(storage, "first") == first
    assert _dump(storage, "second") == second


def test_simulation_checkpoint_preserves_simulated_provenance(storage, tmp_path):
    simulation = PersistentKnowledgeStorage(
        DatabaseManager(str(tmp_path / "separate.db.simulation"), simulation=True)
    )
    try:
        simulation.record_ui_state("first", "fixture", "abcdef", source="synthetic_fixture")
        checkpoint_id = simulation.create_checkpoint("first", "synthetic checkpoint")
        snapshot = _load_snapshot(simulation, checkpoint_id)
        assert snapshot["simulation"] is True
        simulation.delete_memory(game_id="first")
        assert simulation.rollback_checkpoint("first", checkpoint_id)
        assert simulation.get_states("first")[0].source == "synthetic_fixture"

        storage.record_ui_state("first", "live", "abcdef")
        live_checkpoint = storage.create_checkpoint("first", "live checkpoint")
        _store_snapshot(storage, live_checkpoint, snapshot)
        assert not storage.rollback_checkpoint("first", live_checkpoint)
        assert storage.get_states("first")[0].state_name == "live"
    finally:
        simulation.db.close()
