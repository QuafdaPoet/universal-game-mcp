"""Probe capture must not treat a stale target or a fixture as live evidence."""
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest

from ugm.core.capture import CaptureManager, FixtureBackend
from ugm.core.safety import SafetyViolationError
from ugm.core.types import WindowInfo
from ugm.core.window import WindowManager


@pytest.fixture
def window():
    return WindowInfo(
        hwnd=1234, title="Offline test game", process_name="test-game.exe", pid=107,
        x=10, y=20, width=80, height=60,
        client_x=10, client_y=20, client_width=80, client_height=60,
        is_minimized=False, is_focused=True,
    )


@pytest.mark.parametrize("event", ["closed", "reused", "refresh-error"])
def test_current_window_discards_stale_native_identity(monkeypatch, window, event):
    manager = WindowManager()
    manager.attach_window(window)
    monkeypatch.setattr("ugm.core.window.sys", SimpleNamespace(platform="win32"))
    monkeypatch.setitem(__import__("sys").modules, "win32gui", SimpleNamespace(
        IsWindow=lambda hwnd: event != "closed", IsWindowVisible=lambda hwnd: True,
    ))

    def refresh(hwnd):
        if event == "refresh-error":
            raise OSError("window no longer exists")
        return replace(window, pid=window.pid + 1)

    monkeypatch.setattr(manager, "get_window_info", refresh)
    assert manager.current_window is None
    assert manager.current_window is None


def test_probe_rejects_fixture_as_live_evidence(window):
    manager = CaptureManager(backend_override=FixtureBackend(np.zeros((60, 80, 3), np.uint8)))
    with pytest.raises(SafetyViolationError, match="fixture"):
        manager.capture_attached_window(SimpleNamespace(current_window=window), window)


@pytest.mark.parametrize("mutation", [
    {"is_focused": False}, {"client_x": 20}, {"pid": 108},
])
def test_probe_revalidates_target_after_capture(window, mutation):
    bound = SimpleNamespace(current_window=window)

    class Backend:
        def capture(self, **kwargs):
            bound.current_window = replace(window, **mutation)
            return np.zeros((60, 80, 3), np.uint8)

    manager = CaptureManager(backend_override=Backend())
    with pytest.raises(SafetyViolationError, match="target"):
        manager.capture_attached_window(bound, window)


def test_probe_accepts_only_focused_client_sized_frames(window):
    calls = []

    class Backend:
        def capture(self, **kwargs):
            calls.append(kwargs["window"])
            return np.zeros((60, 80, 3), np.uint8)

    manager = CaptureManager(backend_override=Backend())
    bound = SimpleNamespace(current_window=window)
    assert manager.capture_attached_window(bound, window).shape == (60, 80, 3)
    assert calls == [window]
    bound.current_window = replace(window, is_focused=False)
    with pytest.raises(SafetyViolationError, match="target"):
        manager.capture_attached_window(bound, window)
    assert calls == [window]


def test_probe_does_not_run_any_backend_without_an_attachment():
    class Backend:
        def capture(self, **kwargs):
            pytest.fail("An unscoped desktop capture was attempted")

    manager = CaptureManager(backend_override=Backend())
    with pytest.raises(SafetyViolationError, match="target"):
        manager.capture_attached_window(SimpleNamespace(current_window=None), None)


def test_probe_rejects_fallback_fixture_without_returning_frame(monkeypatch, window):
    class Backend:
        def capture(self, **kwargs):
            raise OSError("native capture unavailable")

    manager = CaptureManager(backend_override=Backend())
    monkeypatch.setattr(manager, "_try_next_viable_backend", lambda: FixtureBackend())
    with pytest.raises(SafetyViolationError, match="fixture"):
        manager.capture_attached_window(SimpleNamespace(current_window=window), window)
    assert manager._last_frame is None


def test_current_window_refreshes_geometry_for_same_native_identity(monkeypatch, window):
    manager = WindowManager()
    manager.attach_window(window)
    monkeypatch.setattr("ugm.core.window.sys", SimpleNamespace(platform="win32"))
    monkeypatch.setitem(__import__("sys").modules, "win32gui", SimpleNamespace(
        IsWindow=lambda hwnd: True, IsWindowVisible=lambda hwnd: True,
    ))
    moved = replace(window, client_x=window.client_x + 10, title="Updated game title")
    monkeypatch.setattr(manager, "get_window_info", lambda hwnd: moved)
    assert manager.current_window is moved


def test_probe_rejects_non_client_sized_frame_and_does_not_cache(window):
    class Backend:
        def capture(self, **kwargs):
            return np.zeros((59, 80, 3), np.uint8)

    manager = CaptureManager(backend_override=Backend())
    with pytest.raises(SafetyViolationError, match="does not match"):
        manager.capture_attached_window(SimpleNamespace(current_window=window), window)
    assert manager._last_frame is None


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["capture_screen", "read_text", "explore_interactive_elements"])
async def test_server_never_grabs_desktop_without_an_attachment(tmp_path, monkeypatch, tool):
    from ugm.server import UGMContext, create_server

    class Backend:
        def capture(self, **kwargs):
            pytest.fail("MCP attempted to capture desktop instead of requiring a game")

    monkeypatch.setattr(CaptureManager, "_init_preferred_backend", lambda self: Backend())
    ctx = UGMContext(db_path=tmp_path / "test.db", ocr_backend="mock", skills_pack_dir=None)
    assert not ctx.capture_manager.allow_fixture_fallback
    with pytest.raises(SafetyViolationError, match="attached"):
        await create_server(ctx).call_tool(tool, {})


def test_bound_click_aborts_if_focus_changes_after_mouse_move(monkeypatch, window):
    from ugm.core import input as input_module
    from ugm.core.input import InputController

    bound = SimpleNamespace(current_window=window)
    calls = []

    def move(x, y):
        calls.append(("move", x, y))
        bound.current_window = replace(window, is_focused=False)

    monkeypatch.setattr(input_module, "pydirectinput", SimpleNamespace(
        moveTo=move, click=lambda *args: pytest.fail("Click sent after target lost focus"),
    ))
    monkeypatch.setattr(input_module, "time", SimpleNamespace(perf_counter=lambda: 1.0, sleep=lambda _: None))
    controller = InputController(bound)
    monkeypatch.setattr(controller, "check_corner_abort", lambda: None)
    with pytest.raises(SafetyViolationError, match="focused"):
        controller.click(1.0, 1.0, target_window=window)
    assert calls == [("move", 89, 79)]


def test_bound_press_with_no_native_backend_does_not_claim_success(monkeypatch, window):
    from ugm.core import input as input_module
    from ugm.core.input import InputController

    monkeypatch.setattr(input_module, "pydirectinput", None)
    with pytest.raises(SafetyViolationError, match="backend is unavailable"):
        InputController(SimpleNamespace(current_window=window)).press_keys(["space"], target_window=window)


@pytest.mark.asyncio
async def test_probe_cannot_store_evidence_under_an_unrelated_game(tmp_path, monkeypatch, window):
    from ugm.server import UGMContext, create_server

    ctx = UGMContext(db_path=tmp_path / "test.db", ocr_backend="mock", skills_pack_dir=None)
    ctx.window_manager.set_mock_window(window)
    monkeypatch.setattr(ctx.explorer, "_capture_probe_frame",
                        lambda *args: pytest.fail("Mismatched game_id reached live capture"))
    result = await create_server(ctx).call_tool("probe_action", {
        "action": "wait", "game_id": "unrelated_game",
    })
    data = result.structured_content.get("result", result.structured_content)
    assert data["outcome"] == "blocked"
    assert "game_id does not match" in data["observation_error"]
    assert ctx.storage.get_action_history("unrelated_game") == []
