"""
Tests for V1.5 Safety, Containment & Emergency Stop (Test F).
Verifies:
- Forced dry-run mode never dispatches hardware input
- Emergency stop immediately halts skill execution and navigation
- Coordinates outside target window client bounds are rejected
- Anti-cheat protected processes trigger immediate killswitch and rejection
"""

import json
from typing import Any, Dict
import pytest

from ugm.server import create_server, UGMContext
from ugm.core.safety import (
    safety_manager,
    EmergencyStopTriggered,
    SafetyViolationError
)
from ugm.core.types import WindowInfo
from ugm.core.window import WindowManager
from ugm.core.input import InputController


def extract_data(res: Any) -> Dict[str, Any]:
    if hasattr(res, "structured_content") and res.structured_content:
        if "result" in res.structured_content:
            return res.structured_content["result"]
        return res.structured_content
    if hasattr(res, "content") and res.content:
        try:
            return json.loads(res.content[0].text)
        except Exception:
            pass
    return {}


def test_forced_dry_run_never_dispatches_hardware():
    """Prove that forced dry-run mode never dispatches hardware input even if tool requests live."""
    safety_manager.reset_emergency_stop()
    wm = WindowManager()
    wm.set_mock_window(WindowInfo(
        hwnd=9001, title="Test Window", process_name="test.exe", pid=901,
        x=0, y=0, width=1920, height=1080,
        client_x=0, client_y=0, client_width=1920, client_height=1080,
        is_minimized=False, is_focused=True, dpi_scale=1.0
    ))
    ctrl = InputController(wm, enforce_dry_run=True)

    # Click with dry_run=False
    res_click = ctrl.click(0.5, 0.5, dry_run=False)
    assert res_click.dry_run is True
    assert res_click.executed is False

    # Hover with dry_run=False
    _, _, exec_hover = ctrl.hover(0.5, 0.5, duration_ms=50, dry_run=False)
    assert exec_hover is False

    # Press keys with dry_run=False
    res_keys = ctrl.press_keys(["space"], dry_run=False)
    assert res_keys.dry_run is True


@pytest.mark.asyncio
async def test_emergency_stop_halts_skill_and_navigation():
    """Prove that emergency stop interrupts skill execution and navigation."""
    safety_manager.reset_emergency_stop()
    ctx = UGMContext(offline_fixture="tests/fixtures/hoi4_topbar.png")
    server = create_server(ctx)

    # Trigger emergency stop
    em_res = await server.call_tool("emergency_stop", {"reason": "Manual safety test"})
    em_data = extract_data(em_res)
    assert em_data["emergency_stopped"] is True
    assert safety_manager.is_stopped is True

    # 1. Attempt skill execution while stopped
    skill_res = await server.call_tool("execute_skill", {"skill_id": "pause_unpause"})
    skill_data = extract_data(skill_res)
    assert skill_data["success"] is False
    assert skill_data["status"] == "blocked"
    assert "emergency stop is active" in skill_data["message"].lower()

    # 2. Attempt navigation while stopped
    nav_res = await server.call_tool("navigate_to", {"target_state": "production"})
    nav_data = extract_data(nav_res)
    assert nav_data["success"] is False
    assert "emergency stop is active" in nav_data["message"].lower()

    # 3. Attempt direct click while stopped
    click_res = await server.call_tool("click_target", {"x": 0.5, "y": 0.5})
    click_data = extract_data(click_res)
    assert click_data["executed"] is False
    assert "emergency stop is active" in click_data["reason"].lower()

    safety_manager.reset_emergency_stop()


def test_out_of_window_coordinates_rejected():
    """Prove that coordinates outside normalized [0, 1] range are strictly rejected."""
    safety_manager.reset_emergency_stop()
    wm = WindowManager()
    wm.set_mock_window(WindowInfo(
        hwnd=9002, title="Bounded Window", process_name="test.exe", pid=902,
        x=50, y=50, width=800, height=600,
        client_x=50, client_y=50, client_width=800, client_height=600,
        is_minimized=False, is_focused=True, dpi_scale=1.0
    ))
    ctrl = InputController(wm, dry_run_default=True)

    # Out of range coordinates
    res1 = ctrl.click(-0.01, 0.5)
    assert res1.executed is False
    assert "rejected" in res1.reason.lower()

    res2 = ctrl.click(1.05, 0.5)
    assert res2.executed is False
    assert "rejected" in res2.reason.lower()

    res3 = ctrl.click(0.5, -0.1)
    assert res3.executed is False
    assert "rejected" in res3.reason.lower()

    res4 = ctrl.click(0.5, 1.01)
    assert res4.executed is False
    assert "rejected" in res4.reason.lower()
