"""
Unit tests for UGM input controller (dry-run, window containment, emergency stop).
"""

import pytest
from ugm.core.input import InputController
from ugm.core.window import WindowManager
from ugm.core.types import WindowInfo
from ugm.core.safety import safety_manager, SafetyViolationError, EmergencyStopTriggered


@pytest.fixture
def mock_window():
    return WindowInfo(
        hwnd=8888,
        title="Offline Test Game",
        process_name="test_game.exe",
        pid=123,
        x=100,
        y=100,
        width=1000,
        height=600,
        client_x=100,
        client_y=100,
        client_width=1000,
        client_height=600,
        is_minimized=False,
        is_focused=True
    )


def test_input_dry_run_hover(mock_window):
    wm = WindowManager(mock_window=mock_window)
    controller = InputController(wm, dry_run_default=True)

    screen_x, screen_y, executed = controller.hover(0.5, 0.5, duration_ms=50)
    assert screen_x == 600
    assert screen_y == 400
    assert executed is False  # In dry run, executed is False


def test_input_dry_run_click(mock_window):
    wm = WindowManager(mock_window=mock_window)
    controller = InputController(wm, dry_run_default=True)

    res = controller.click(0.2, 0.3, button="left", clicks=1)
    assert res.dry_run is True
    assert res.executed is False
    assert res.x_pixel == 300
    assert res.y_pixel == 280
    assert "Dry-run simulated" in res.reason


def test_input_blocked_by_emergency_stop(mock_window):
    safety_manager.reset_emergency_stop()
    safety_manager.trigger_emergency_stop("Killswitch activated")

    wm = WindowManager(mock_window=mock_window)
    controller = InputController(wm, dry_run_default=True)

    res = controller.click(0.5, 0.5)
    assert res.executed is False
    assert "Emergency stop is active" in res.reason

    with pytest.raises(EmergencyStopTriggered):
        controller.press_keys(["space"])

    safety_manager.reset_emergency_stop()


def test_input_out_of_bounds_rejection(mock_window):
    wm = WindowManager(mock_window=mock_window)
    controller = InputController(wm, dry_run_default=True)

    res = controller.click(1.5, 0.5)  # Outside normalized range
    assert res.executed is False
    assert "rejected" in res.reason.lower()
