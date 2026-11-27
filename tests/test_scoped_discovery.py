"""Observation helpers cannot read or classify an unrelated desktop/game."""
from dataclasses import replace

import numpy as np
import pytest

from ugm.core.safety import SafetyViolationError
from ugm.core.types import WindowInfo
from ugm.server import UGMContext, create_server


@pytest.fixture
def native(tmp_path, monkeypatch):
    ctx = UGMContext(db_path=tmp_path / "test.db", ocr_backend="mock", skills_pack_dir=None)
    def reject(*args, **kwargs):
        pytest.fail("Unexpected unscoped grab")
    monkeypatch.setattr(ctx.capture_manager, "capture", reject)
    yield ctx
    ctx.storage.db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "args"), [
    ("locate_icon", {"icon_name": "pause_btn"}),
    ("locate_ui_element", {"text_query": "Play"}),
    ("explore_interactive_elements", {}),
    ("get_navigation_plan", {"target_state": "menu"}),
])
async def test_no_unattached_capture(native, name, args):
    with pytest.raises(SafetyViolationError, match="attached"):
        await create_server(native).call_tool(name, args)


@pytest.mark.asyncio
async def test_unrelated_route_cannot_trigger_live_frame(native, monkeypatch):
    win = WindowInfo(1, "Offline Game", "testgame.exe", 42, 0, 0, 80, 60, 0, 0, 80, 60, False, True)
    native.window_manager.set_mock_window(win)
    def reject(*args, **kwargs):
        pytest.fail("Captured frame for unrelated game")
    monkeypatch.setattr(native.capture_manager, "capture_attached_window", reject)
    with pytest.raises(SafetyViolationError, match="game_id"):
        await create_server(native).call_tool("get_navigation_plan", {
            "game_id": "other", "target_state": "menu",
        })


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "args"), [
    ("locate_ui_element", {"text_query": "Play"}),
    ("explore_interactive_elements", {"scan_mode": "shapes"}),
])
async def test_reattach_during_capture_rejects_observation(native, monkeypatch, name, args):
    win = WindowInfo(1, "Offline Game", "testgame.exe", 42, 0, 0, 80, 60, 0, 0, 80, 60, False, True)
    native.window_manager.set_mock_window(win)
    def capture(*args, **kwargs):
        native.window_manager.set_mock_window(replace(win, hwnd=2))
        return np.zeros((60, 80, 3), dtype=np.uint8)
    monkeypatch.setattr(native.capture_manager, "capture_attached_window", capture)
    with pytest.raises(SafetyViolationError, match="changed"):
        await create_server(native).call_tool(name, args)


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "args"), [
    ("locate_ui_element", {"text_query": "Play", "roi": []}),
    ("explore_interactive_elements", {"roi": [.9, .1, .2, .1]}),
    ("explore_interactive_elements", {"scan_mode": "typo"}),
])
async def test_invalid_request_rejected_before_capture(native, monkeypatch, name, args):
    def reject(*args, **kwargs):
        pytest.fail("Invalid request attempted capture")
    monkeypatch.setattr(native, "capture_verification_frame", reject)
    with pytest.raises(ValueError):
        await create_server(native).call_tool(name, args)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["ambiguous", "unknown"])
async def test_implicit_route_declines_unrecognized_start(native, monkeypatch, status):
    from types import SimpleNamespace

    def recognize(*args, **kwargs):
        assert kwargs["game_id"] == "example"
        assert kwargs["record_observation"] is False
        return SimpleNamespace(status=status, state_name="guessed")
    def reject(*args, **kwargs):
        pytest.fail("Looked up route for unrecognized start")
    monkeypatch.setattr(native, "capture_verification_frame", lambda **kwargs: (
        np.zeros((10, 10, 3), dtype=np.uint8),
        SimpleNamespace(native_game_id="example", simulation=False),
    ))
    monkeypatch.setattr(native.state_recognizer, "recognize_state", recognize)
    monkeypatch.setattr(native, "get_navigation_graph", reject)
    monkeypatch.setattr(native, "require_observation_scope", lambda scope: None)
    result = await create_server(native).call_tool("get_navigation_plan", {"target_state": "menu"})
    data = result.structured_content.get("result", result.structured_content)
    assert not data["found"] and not data["steps"] and data["total_cost"] is None


@pytest.mark.asyncio
async def test_locator_rejects_same_target_reattach_before_hover(native, monkeypatch):
    win = WindowInfo(1, "Offline Game", "testgame.exe", 42, 0, 0, 80, 60, 0, 0, 80, 60, False, True)
    native.window_manager.set_mock_window(win)
    monkeypatch.setattr(native.capture_manager, "capture_attached_window",
                        lambda *args: np.zeros((60, 80, 3), dtype=np.uint8))
    def resolve(**kwargs):
        native.window_manager.set_mock_window(win)
        return kwargs["hover_reader"](norm_x=.1, norm_y=.1, duration_ms=10, dry_run=False)
    def forbid(*args, **kwargs):
        pytest.fail("An old OCR snapshot tried to hover on a reattached window")
    monkeypatch.setattr(native.target_resolver, "resolve_target", resolve)
    monkeypatch.setattr(native.verifier, "hover_and_read_tooltip", forbid)
    with pytest.raises(SafetyViolationError, match="changed"):
        await create_server(native).call_tool("locate_ui_element", {"text_query": "Play"})


@pytest.mark.asyncio
async def test_goal_scan_cannot_publish_frame_after_focus_loss(native, monkeypatch):
    win = WindowInfo(1, "Offline Game", "testgame.exe", 42, 0, 0, 80, 60, 0, 0, 80, 60, False, True)
    native.window_manager.set_mock_window(win)
    monkeypatch.setattr(native.capture_manager, "capture_attached_window",
                        lambda *args: np.zeros((60, 80, 3), dtype=np.uint8))
    def scan(*args, **kwargs):
        # Windows reports live focus changes without reattaching.
        monkeypatch.setattr(type(native.window_manager), "current_window",
                            property(lambda _: replace(win, is_focused=False)))
        return []
    monkeypatch.setattr(native.explorer, "discover_interactive_elements", scan)
    with pytest.raises(SafetyViolationError, match="blurred"):
        await create_server(native).call_tool("explore_interactive_elements", {"goal": "play"})
