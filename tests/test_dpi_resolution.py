"""
Unit tests for DPI scaling, resolution adaptation, client-area offsets,
window focus enforcement, and emergency stop containment.
"""

import pytest
from ugm.core.types import WindowInfo, NormalizedRect, BoundingBox
from ugm.core.window import WindowManager
from ugm.core.input import InputController
from ugm.core.safety import safety_manager, SafetyViolationError, EmergencyStopTriggered


def test_window_geometry_client_offsets():
    # Window with borders and title bar:
    # Full window rect: x=100, y=50, width=1936, height=1119
    # Client rect: client_x=108, client_y=81, client_width=1920, client_height=1080
    win = WindowInfo(
        hwnd=5555,
        title="DPI Test Game",
        process_name="game.exe",
        pid=789,
        x=100,
        y=50,
        width=1936,
        height=1119,
        client_x=108,
        client_y=81,
        client_width=1920,
        client_height=1080,
        is_minimized=False,
        is_focused=True,
        dpi_scale=1.25
    )

    wm = WindowManager(mock_window=win)
    ctrl = InputController(wm, dry_run_default=True)

    # Test center coordinate (0.5, 0.5)
    # Expected screen X: client_x + 0.5 * 1920 = 108 + 960 = 1068
    # Expected screen Y: client_y + 0.5 * 1080 = 81 + 540 = 621
    screen_x, screen_y, w_obj = ctrl._resolve_screen_coords(0.5, 0.5)
    assert screen_x == 1068
    assert screen_y == 621
    assert w_obj.dpi_scale == 1.25


def test_resolution_change_scaling():
    # 1440p resolution (2560 x 1440)
    win_1440p = WindowInfo(
        hwnd=5556,
        title="1440p Strategy Game",
        process_name="game.exe",
        pid=790,
        x=0,
        y=0,
        width=2560,
        height=1440,
        client_x=0,
        client_y=0,
        client_width=2560,
        client_height=1440,
        is_minimized=False,
        is_focused=True,
        dpi_scale=1.5
    )

    wm = WindowManager(mock_window=win_1440p)
    ctrl = InputController(wm, dry_run_default=True)

    # Top-right button normalized (0.9, 0.05)
    screen_x, screen_y, _ = ctrl._resolve_screen_coords(0.9, 0.05)
    assert screen_x == int(0.9 * 2560)
    assert screen_y == int(0.05 * 1440)


def test_window_not_selected_rejection():
    wm = WindowManager(mock_window=None)
    ctrl = InputController(wm, dry_run_default=True)

    with pytest.raises(SafetyViolationError, match="No target game window selected"):
        ctrl._resolve_screen_coords(0.5, 0.5)


def test_window_minimized_rejection():
    win_min = WindowInfo(
        hwnd=5557,
        title="Minimized Game",
        process_name="game.exe",
        pid=791,
        x=0,
        y=0,
        width=1920,
        height=1080,
        client_x=0,
        client_y=0,
        client_width=1920,
        client_height=1080,
        is_minimized=True,
        is_focused=False,
        dpi_scale=1.0
    )
    wm = WindowManager(mock_window=win_min)
    ctrl = InputController(wm, dry_run_default=True)

    with pytest.raises(SafetyViolationError, match="is minimized"):
        ctrl._resolve_screen_coords(0.5, 0.5)


def test_emergency_stop_kills_all_subsequent_inputs():
    win = WindowInfo(
        hwnd=5558,
        title="Active Game",
        process_name="game.exe",
        pid=792,
        x=0,
        y=0,
        width=1920,
        height=1080,
        client_x=0,
        client_y=0,
        client_width=1920,
        client_height=1080,
        is_minimized=False,
        is_focused=True,
        dpi_scale=1.0
    )
    wm = WindowManager(mock_window=win)
    ctrl = InputController(wm, dry_run_default=True)

    # Ensure normal input works
    safety_manager.reset_emergency_stop()
    res = ctrl.click(0.5, 0.5)
    assert res.dry_run is True

    # Trigger emergency stop
    safety_manager.trigger_emergency_stop(reason="User emergency stop test")
    assert safety_manager.is_stopped is True

    # Click should be rejected
    res2 = ctrl.click(0.5, 0.5)
    assert res2.executed is False
    assert "Emergency stop is active" in res2.reason

    # Keypress should be rejected
    with pytest.raises(EmergencyStopTriggered):
        ctrl.press_keys(["space"])

    safety_manager.reset_emergency_stop()
