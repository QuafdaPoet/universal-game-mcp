"""The learned skill natural key is (game_id, skill_id), not skill_id."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
from threading import Barrier

import pytest

from ugm.memory.database import DatabaseManager
from ugm.memory.storage import PersistentKnowledgeStorage
from ugm.server import UGMContext, create_server
from ugm.skills.engine import SkillExecutionEngine
from ugm.skills.learner import SkillLearner


STEPS = [{"action": "press_keys", "params": {"keys": ["space"]}}]
NEW_STEPS = [{"action": "press_keys", "params": {"keys": ["escape"]}}]


@pytest.fixture
def storage(tmp_path):
    db = DatabaseManager(str(tmp_path / "skills.sqlite3"))
    try:
        yield PersistentKnowledgeStorage(db)
    finally:
        db.close()


def _make_legacy_db(path, skill_id="pause"):
    """Create the exact v2 learned_skill table, including nullable legacy PK."""
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at REAL NOT NULL)")
        DatabaseManager.__new__(DatabaseManager)._migrate(conn, from_ver=0, to_ver=2)
        conn.execute("""
            INSERT INTO learned_skills VALUES (?, 'game_a', '1.2.3', 'Pause', 'Starts pause',
                '{"window_focused": true}', '[]', '{}', 'verified', 9, 8, 1, 0.9, 'live',
                150.1, 160.2, 3, 'A note')
        """, (skill_id,))
        return conn.execute("SELECT * FROM learned_skills").fetchone()


def test_v2_migration_preserves_all_fields_and_supports_same_id(tmp_path):
    path = tmp_path / "legacy.db"
    old_row = _make_legacy_db(path)
    db = DatabaseManager(str(path))
    try:
        conn = db.get_connection()
        assert conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] >= 3
        cols = {row["name"]: row["pk"] for row in conn.execute("PRAGMA table_info(learned_skills)")}
        assert (cols["game_id"], cols["skill_id"]) == (1, 2)
        assert tuple(conn.execute("SELECT * FROM learned_skills").fetchone()) == old_row
        assert conn.execute("PRAGMA integrity_check").fetchall()[0][0] == "ok"
        assert [row["name"] for row in conn.execute("PRAGMA index_info(idx_learned_skills_game)")] == [
            "game_id", "status"
        ]
        PersistentKnowledgeStorage(db).save_learned_skill("pause", "game_b", "Pause other game", STEPS)
    finally:
        db.close()

    reopened = DatabaseManager(str(path))
    try:
        assert reopened.get_connection().execute("SELECT COUNT(*) FROM learned_skills").fetchone()[0] == 2
    finally:
        reopened.close()


def test_failed_migration_rolls_back_legacy_table(tmp_path):
    path = tmp_path / "invalid_legacy.db"
    old_row = _make_legacy_db(path, skill_id=None)
    with pytest.raises(sqlite3.IntegrityError):
        DatabaseManager(str(path))

    with sqlite3.connect(path) as conn:
        assert tuple(conn.execute("SELECT * FROM learned_skills").fetchone()) == old_row
        assert conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == 2
        assert not conn.execute("SELECT name FROM sqlite_master WHERE name = 'learned_skills_v3'").fetchone()


def test_same_skill_id_is_game_scoped_across_repository_operations(storage):
    for game in ("game_a", "game_b"):
        storage.save_learned_skill("pause", game, f"Original {game}", STEPS)
    storage.save_learned_skill("pause", "game_b", "Updated B", NEW_STEPS)

    # Backward-compatible unscoped calls refuse ambiguous IDs.
    assert storage.get_learned_skill("pause") is None
    assert storage.update_skill_stats("pause", False)["updated"] is False
    assert storage.optimize_learned_skill("pause", NEW_STEPS) is False
    assert storage.delete_learned_skill("pause") is False

    a_before = storage.get_learned_skill("pause", game_id="game_a")
    b_before = storage.get_learned_skill("pause", game_id="game_b")
    assert a_before.name == "Original game_a"
    assert b_before.name == "Updated B"
    assert a_before.steps == STEPS
    assert b_before.steps == NEW_STEPS
    assert storage.get_learned_skill("pause", game_id="unknown") is None

    assert storage.update_skill_stats("pause", True, game_id="game_a")["updated"]
    assert storage.update_skill_stats("pause", False, is_execution_error=True, game_id="game_a")["updated"]
    assert storage.optimize_learned_skill("pause", NEW_STEPS, game_id="game_a")
    assert storage.get_learned_skill("pause", game_id="game_b") == b_before
    assert storage.delete_learned_skill("pause", game_id="game_a")
    assert storage.get_learned_skill("pause") == b_before


def test_checkpoint_restoring_same_id_does_not_touch_other_game(storage):
    storage.save_learned_skill("pause", "game_a", "A", STEPS)
    checkpoint = storage.create_checkpoint("game_a", "before edits")
    storage.save_learned_skill("pause", "game_b", "B", NEW_STEPS)
    b_before = storage.get_learned_skill("pause", game_id="game_b")
    storage.save_learned_skill("pause", "game_a", "A edited", NEW_STEPS)

    assert storage.rollback_checkpoint("game_a", checkpoint)
    assert storage.get_learned_skill("pause", game_id="game_a").steps == STEPS
    assert storage.get_learned_skill("pause", game_id="game_b") == b_before


def test_skill_engine_lookup_and_learner_mutation_use_game_id(storage):
    for game in ("game_a", "game_b"):
        storage.save_learned_skill("pause", game, f"Original {game}", STEPS)

    engine = SkillExecutionEngine(None, None, None, None, None, None, storage=storage)
    learner = SkillLearner(storage, engine)
    assert engine.lookup_skill("pause", game_id="game_a")["name"] == "Original game_a"
    assert engine.lookup_skill("pause", game_id="game_b")["name"] == "Original game_b"
    assert engine.lookup_skill("pause") is None
    assert learner.approve_skill("pause", game_id="game_b")
    assert learner.reject_skill("pause", game_id="game_a")
    assert storage.get_learned_skill("pause", game_id="game_b").status == "verified"
    assert storage.get_learned_skill("pause", game_id="game_a").status == "rejected"


def test_concurrent_validations_do_not_drop_updates(storage):
    storage.save_learned_skill("pause", "game_a", "Pause", STEPS)
    ready = Barrier(4)

    def update_in_worker():
        try:
            ready.wait(timeout=5)
            for _ in range(20):
                assert storage.update_skill_stats("pause", True, game_id="game_a")["updated"]
        finally:
            storage.db.close()

    with ThreadPoolExecutor(max_workers=4) as executor:
        pending = [executor.submit(update_in_worker) for _ in range(4)]
        for future in pending:
            future.result()

    skill = storage.get_learned_skill("pause", game_id="game_a")
    assert (skill.validation_count, skill.success_count) == (81, 81)


def _structured_payload(result):
    if result.structured_content:
        return result.structured_content.get("result", result.structured_content)
    return json.loads(result.content[0].text)


@pytest.mark.asyncio
async def test_mcp_delete_and_optimize_respect_game_scope(tmp_path):
    fixture = Path(__file__).parent / "fixtures" / "hoi4_topbar.png"
    ctx = UGMContext(offline_fixture=str(fixture), db_path=str(tmp_path / "mcp.db"))
    server = create_server(ctx)
    try:
        for game in ("game_a", "game_b"):
            result = await server.call_tool("learn_skill", {
                "skill_id": "shared_pause", "game_id": game,
                "name": f"Pause {game}", "steps": STEPS,
            })
            assert _structured_payload(result)["success"] is True

        for args in ({}, {"game_id": "wrong_game"}):
            delete = await server.call_tool("delete_learned_memory", {"skill_id": "shared_pause", **args})
            assert _structured_payload(delete)["deleted"] is False
            optimize = await server.call_tool("optimize_skill", {
                "skill_id": "shared_pause", "steps": NEW_STEPS, **args,
            })
            assert _structured_payload(optimize)["success"] is False

        optimized = await server.call_tool("optimize_skill", {
            "skill_id": "shared_pause", "game_id": "game_b", "steps": NEW_STEPS,
        })
        assert _structured_payload(optimized)["success"] is True
        a = ctx.storage.get_learned_skill("shared_pause", game_id="game_a")
        b = ctx.storage.get_learned_skill("shared_pause", game_id="game_b")
        assert a.steps == STEPS
        assert b.steps == NEW_STEPS

        deleted = await server.call_tool("delete_learned_memory", {
            "skill_id": "shared_pause", "game_id": "game_a",
        })
        assert _structured_payload(deleted)["deleted"] is True
        assert ctx.storage.get_learned_skill("shared_pause", game_id="game_a") is None
        assert ctx.storage.get_learned_skill("shared_pause", game_id="game_b") == b
    finally:
        ctx.storage.db.close()
