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
    monkeypatch.setenv("UGM_SEMANTIC_SELECTOR_LEARNING", "1")
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


async def learn_screen(server, ctx, win, screen):
    for pixels in (1, 2, 3):
        screen.pixels = pixels
        result = await call(server, record=True)
        if pixels == 2:
            ctx.window_manager.set_mock_window(win)
    assert result["status"] == "provisional_match"
    return result["provisional_id"]


async def observe_selector(server, identity, record=False):
    args = {"selector_id": "open_settings", "label": "Settings", "record": record, "provisional_id": identity}
    out = await server.call_tool("observe_ui_selector", args)
    return out.structured_content.get("result", out.structured_content)


@pytest.mark.asyncio
async def test_anonymous_selector_reuses_two_native_captures_and_separates_evidence(new_game, monkeypatch):
    from ugm.memory.selector_evidence import SelectorEvidenceStore, SelectorRecipe

    ctx, server, win, screen = new_game
    identity = await learn_screen(server, ctx, win, screen)
    scans = []
    read = ctx.ocr_engine.read_text
    def track(*a, **kw):
        scans.append(screen.calls)
        return read(*a, **kw)
    monkeypatch.setattr(ctx.ocr_engine, "read_text", track)
    monkeypatch.setattr(ctx.state_recognizer, "recognize_state", lambda *a, **kw: pytest.fail("curated recognizer"))
    for pixels in (4, 5, 6):
        screen.pixels = pixels
        out = await observe_selector(server, identity, record=True)
        assert out["match"] == "found" and out["provisional_id"] == identity
        assert out["state_name"] == "unknown" and out["bbox_normalized"] is None
        assert not out["interactivity_verified"] and not out["action_suggested"]
        if pixels == 5:
            ctx.window_manager.set_mock_window(win)
    out = await observe_selector(server, identity)
    assert out["evidence"]["status"] == "repeat_observed"
    assert out["evidence"]["observations"] == 3
    assert scans == list(range(7, 15))
    assert ctx.storage.get_states("newgame") == []
    for state in ("provisional:" + identity, " PROVISIONAL:" + identity):
        with pytest.raises(ValueError, match="reserved"):
            SelectorEvidenceStore(ctx.storage.db).status(SelectorRecipe("newgame", state, "open_settings", "Settings"))


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["different_id", "other_game", "stale", "ambiguous", "candidate", "rollback"])
async def test_anonymous_selector_fails_closed_without_writing(new_game, failure):
    from dataclasses import replace

    ctx, server, win, screen = new_game
    checkpoint = ctx.storage.create_checkpoint("newgame", "before")
    if failure == "candidate":
        identity = (await call(server, record=True))["provisional_id"]
    else:
        identity = await learn_screen(server, ctx, win, screen)
    if failure == "different_id":
        identity = "provisional_" + "a" * 32
    if failure == "other_game":
        ctx.window_manager.set_mock_window(replace(win, process_name="othergame.exe"))
    if failure == "stale":
        with ctx.storage.db.get_connection() as conn:
            conn.execute("UPDATE provisional_screen_receipts SET recorded_at=0")
    if failure == "ambiguous":
        screen.readings[-1] = "Load Game"
    if failure == "rollback":
        assert ctx.storage.rollback_checkpoint("newgame", checkpoint)
    out = await observe_selector(server, identity, record=True)
    assert out["match"] == "provisional_unconfirmed"
    assert not out["interactivity_verified"] and out["bbox_normalized"] is None
    assert ctx.storage.db.get_connection().execute("SELECT count(*) FROM semantic_selector_receipts").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_anonymous_selector_rejects_scope_change_and_weak_sources(new_game, monkeypatch):
    from ugm.core.ocr import MockOCREngine

    ctx, server, win, screen = new_game
    identity = await learn_screen(server, ctx, win, screen)
    for field, target, message in (
        ("dry_run_default", ctx.input_controller, "dry.run"),
        ("_offline_fixture_configured", ctx, "fixture"),
    ):
        with monkeypatch.context() as m:
            m.setattr(target, field, True)
            with pytest.raises(SafetyViolationError, match=message):
                await observe_selector(server, identity)
    with monkeypatch.context() as m:
        m.setattr(ctx, "ocr_engine", MockOCREngine())
        with pytest.raises(SafetyViolationError, match="real OCR"):
            await observe_selector(server, identity)
    baseline = screen.calls
    read = ctx.ocr_engine.read_text
    def switch(*a, **kw):
        out = read(*a, **kw)
        if screen.calls == baseline + 2:
            ctx.window_manager.set_mock_window(win)
        return out
    monkeypatch.setattr(ctx.ocr_engine, "read_text", switch)
    with pytest.raises(SafetyViolationError, match="changed"):
        await observe_selector(server, identity, record=True)
    assert ctx.storage.db.get_connection().execute("SELECT count(*) FROM semantic_selector_receipts").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_both_fresh_frames_independently_rematch_stored_identity(new_game, monkeypatch):
    ctx, server, win, screen = new_game
    identity = await learn_screen(server, ctx, win, screen)
    capture = ctx.capture_manager.capture_attached_window
    colors = iter([41, 35])  # Agree with each other, but the first does not match stored 30.
    def captures(*a, **kw):
        screen.color = next(colors)
        return capture(*a, **kw)
    monkeypatch.setattr(ctx.capture_manager, "capture_attached_window", captures)
    out = await observe_selector(server, identity, record=True)
    assert out["match"] == "provisional_unconfirmed"
    assert ctx.storage.db.get_connection().execute("SELECT count(*) FROM semantic_selector_receipts").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_selector_checks_label_from_second_ocr_only(new_game, monkeypatch):
    ctx, server, win, screen = new_game
    identity = await learn_screen(server, ctx, win, screen)
    read = ctx.ocr_engine.read_text
    baseline = screen.calls
    def with_dynamic_label(*a, **kw):
        out = read(*a, **kw)
        if screen.calls == baseline + 1:
            out.items.append(item("Queue 3", .7))  # Numerals are not screen anchors.
        return out
    monkeypatch.setattr(ctx.ocr_engine, "read_text", with_dynamic_label)
    out = await server.call_tool("observe_ui_selector", {
        "selector_id": "queue", "label": "Queue 3", "provisional_id": identity,
    })
    assert out.structured_content.get("result", out.structured_content)["match"] == "not_found"
    assert screen.calls == baseline + 2
