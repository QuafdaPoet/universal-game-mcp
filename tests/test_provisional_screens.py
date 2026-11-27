from types import SimpleNamespace

import numpy as np
import pytest

from ugm.core.safety import SafetyViolationError
from ugm.core.types import NormalizedRect, OCRItem, WindowInfo
from ugm.server import UGMContext, create_server


def item(text, x):
    return OCRItem(text, .96, None, NormalizedRect(x, .1, .10, .08))


@pytest.fixture
def new_game(tmp_path, monkeypatch):
    monkeypatch.setenv("UGM_PROVISIONAL_UI_LEARNING", "1")
    ctx = UGMContext(db_path=tmp_path / "db", ocr_backend="mock", skills_pack_dir=None)
    win = WindowInfo(42, "New Game", "newgame.exe", 1000, 0, 0, 90, 60, 0, 0, 90, 60, False, True)
    ctx.window_manager.set_mock_window(win)
    screen = SimpleNamespace(color=30, readings=["Play", "Settings", "Credits"], pixels=1, calls=0)
    def capture(*a, **kw):
        screen.calls += 1
        frame = np.full((60, 90, 3), screen.color, dtype=np.uint8)
        frame[0, 0, 0] = screen.pixels
        return frame
    monkeypatch.setattr(ctx.capture_manager, "capture_attached_window", capture)
    ctx.ocr_engine = SimpleNamespace(read_text=lambda *a, **kw: SimpleNamespace(
        items=[item(label, .1 + index * .15) for index, label in enumerate(screen.readings)]))
    monkeypatch.setattr(ctx.storage, "record_ui_state", lambda *a, **kw: pytest.fail("legacy state write"))
    for action in ("click", "hover", "press_keys"):
        monkeypatch.setattr(ctx.input_controller, action, lambda *a, **kw: pytest.fail("unexpected input"))
    yield ctx, create_server(ctx), win, screen
    ctx.storage.db.close()


async def call(server, **kw):
    result = await server.call_tool("identify_provisional_ui", kw)
    return result.structured_content.get("result", result.structured_content)


@pytest.mark.asyncio
async def test_repeated_evidence_bootstraps_anonymous_identity_without_pack(new_game):
    ctx, server, win, screen = new_game
    create = await call(server, record=True)
    assert create["status"] == "provisional_candidate"
    identity = create["provisional_id"]
    assert (await call(server, record=True))["receipt_count"] == 1
    screen.pixels = 2
    assert (await call(server, record=True))["receipt_count"] == 2
    screen.pixels = 3
    assert (await call(server, record=True))["status"] == "provisional_candidate"
    screen.pixels = 4
    ctx.window_manager.set_mock_window(win)
    again = await call(server, record=True)
    assert again["status"] == "provisional_match" and again["provisional_id"] == identity
    fresh = await call(server)
    assert fresh["provisional_id"] == identity and fresh["receipt_count"] == 4
    assert fresh["state_name"] == "unknown" and not fresh["action_suggested"]
    assert ctx.storage.get_states("newgame") == []


@pytest.mark.asyncio
async def test_near_lookalike_is_ambiguous_and_does_not_seed_second_identity(new_game):
    ctx, server, _, screen = new_game
    await call(server, record=True)
    screen.readings[-1] = "Load Game"
    out = await call(server, record=True)
    assert out["status"] == "ambiguous" and out["provisional_id"] is None
    assert ctx.storage.db.get_connection().execute("SELECT count(*) FROM provisional_screens").fetchone()[0] == 1


@pytest.mark.asyncio
async def test_default_off_dry_run_mock_and_read_only_are_isolated(new_game):
    ctx, server, _, screen = new_game
    ctx._provisional_ui_learning = False
    with pytest.raises(SafetyViolationError, match="disabled"):
        await call(server, record=True)
    ctx._provisional_ui_learning = True
    ctx.input_controller.dry_run_default = True
    with pytest.raises(SafetyViolationError, match="dry.run"):
        await call(server, record=True)
    ctx.input_controller.dry_run_default = False
    assert screen.calls == 0
    assert (await call(server))["status"] == "unseen"
    assert ctx.storage.db.get_connection().execute("SELECT count(*) FROM provisional_screens").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_attachment_change_during_observation_drops_receipt(new_game, monkeypatch):
    ctx, server, win, screen = new_game
    read = ctx.ocr_engine.read_text
    def changed(*a, **kw):
        result = read(*a, **kw)
        if screen.calls == 2:
            ctx.window_manager.set_mock_window(win)
        return result
    monkeypatch.setattr(ctx.ocr_engine, "read_text", changed)
    with pytest.raises(SafetyViolationError, match="changed"):
        await call(server, record=True)
    assert ctx.storage.db.get_connection().execute("SELECT count(*) FROM provisional_screens").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_distinct_screens_and_equal_looking_games_are_partitioned(new_game):
    from dataclasses import replace

    ctx, server, win, screen = new_game
    first = await call(server, record=True)
    screen.color = 200
    screen.readings = ["Diplomacy", "Campaign", "Supply Line"]
    later = await call(server, record=True)
    assert later["status"] == "provisional_candidate"
    assert later["provisional_id"] != first["provisional_id"]
    ctx.window_manager.set_mock_window(replace(win, process_name="othergame.exe"))
    assert (await call(server))["status"] == "unseen"
    another = await call(server, record=True)
    assert another["provisional_id"] != later["provisional_id"]
    rows = ctx.storage.db.get_connection().execute("SELECT anchors FROM provisional_screens").fetchall()
    assert len(rows) == 3
    assert "campaign" not in repr([r["anchors"] for r in rows]).casefold()


@pytest.mark.asyncio
async def test_v7_storage_survives_reopen_and_rollback_drops_only_current_game(new_game):
    from dataclasses import replace
    from ugm.memory.database import DatabaseManager

    ctx, server, win, _ = new_game
    checkpoint = ctx.storage.create_checkpoint("newgame", "before")
    original = await call(server, record=True)
    db = DatabaseManager(ctx.storage.db.db_path)
    try:
        ver = db.get_connection().execute("SELECT max(version) FROM schema_version").fetchone()[0]
        assert ver >= 7
        assert db.get_connection().execute("SELECT screen_id FROM provisional_screens").fetchone()[0] == original["provisional_id"]
    finally:
        db.close()
    ctx.window_manager.set_mock_window(replace(win, process_name="othergame.exe"))
    other = await call(server, record=True)
    assert ctx.storage.rollback_checkpoint("newgame", checkpoint)
    assert (await call(server))["provisional_id"] == other["provisional_id"]
    ctx.window_manager.set_mock_window(win)
    assert (await call(server))["status"] == "unseen"


@pytest.mark.asyncio
async def test_mock_ocr_does_not_create_anonymous_identity(new_game):
    from ugm.core.ocr import MockOCREngine

    ctx, server, _, observation = new_game
    ctx.ocr_engine = MockOCREngine()
    with pytest.raises(SafetyViolationError, match="real OCR"):
        await call(server, record=True)
    assert observation.calls == 0
