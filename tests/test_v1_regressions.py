"""
Regression tests for V1 defect audits:
1. Server-wide dry-run cannot be overridden by individual tool passing dry_run=False
2. click_target(selector_id=...) resolves registered selectors using loaded Game Skill Pack
3. Capture and verification consistently operate on selected game window client rect
4. Missing or invalid verification baselines produce an explicit failure rather than accidental success
5. Runtime capture fallback tries viable alternatives instead of silently switching to blank fixture
6. Input focus checks, bounds checks, emergency-stop handling, and anti-cheat restrictions
7. Benchmark separation between synthetic in-memory copy and real screen capture
"""

import os
import json
import numpy as np
import pytest

from ugm.core.safety import (
    SafetyManager,
    safety_manager,
    EmergencyStopTriggered,
    SafetyViolationError
)
from ugm.core.window import WindowManager
from ugm.core.types import WindowInfo, NormalizedRect, TargetStatus
from ugm.core.input import InputController
from ugm.core.capture import CaptureManager, FixtureBackend, BaseCaptureBackend
from ugm.core.verifier import ActionVerifier
from ugm.core.ocr import BaseOCREngine
from ugm.server import create_server, UGMContext


def extract_data(res):
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


class FailingBackend(BaseCaptureBackend):
    def capture(self, window=None, roi=None):
        raise RuntimeError("Simulated DirectX driver crash")


def test_dry_run_override_rejected():
    """Defect 1: dry_run=False must NOT override server-wide dry-run or enforce_dry_run."""
    safety_manager.reset_emergency_stop()
    wm = WindowManager()
    wm.set_mock_window(WindowInfo(
        hwnd=1001,
        title="Test Window",
        process_name="test.exe",
        pid=101,
        x=0, y=0, width=800, height=600,
        client_x=0, client_y=0, client_width=800, client_height=600,
        is_minimized=False, is_focused=True, dpi_scale=1.0
    ))
    ctrl = InputController(wm, enforce_dry_run=True)

    # Calling click with explicit dry_run=False
    res = ctrl.click(0.5, 0.5, dry_run=False)
    assert res.dry_run is True
    assert res.executed is False

    # Calling hover with explicit dry_run=False
    _, _, executed = ctrl.hover(0.5, 0.5, duration_ms=50, dry_run=False)
    assert executed is False

    # Calling press_keys with explicit dry_run=False
    action_res = ctrl.press_keys(["space"], dry_run=False)
    assert action_res.dry_run is True

    # Global safety manager dry-run enforcement
    safety_manager.set_dry_run_enforced(True)
    ctrl_normal = InputController(wm, enforce_dry_run=False)
    res_enforced = ctrl_normal.click(0.5, 0.5, dry_run=False)
    assert res_enforced.dry_run is True
    assert res_enforced.executed is False

    safety_manager.set_dry_run_enforced(False)


@pytest.mark.asyncio
async def test_click_target_resolves_selector_id():
    """Defect 2: click_target(selector_id=...) must resolve registered selectors from skill pack."""
    safety_manager.reset_emergency_stop()
    ctx = UGMContext(offline_fixture="tests/fixtures/hoi4_topbar.png")
    ctx.input_controller.dry_run_default = True
    server = create_server(ctx)

    # Call click_target with selector_id only
    res = await server.call_tool("click_target", {
        "selector_id": "pause_button",
        "dry_run": True
    })
    data = extract_data(res)
    assert data["status"] == "success"
    assert data["dry_run"] is True
    assert data["x_normalized"] > 0.0
    assert data["y_normalized"] >= 0.0


def test_window_movement_and_geometry():
    """Defect 3: WindowManager refreshes geometry and bounds checks contain client rect."""
    wm = WindowManager()
    mock_win = WindowInfo(
        hwnd=2002,
        title="Moved Game Window",
        process_name="game.exe",
        pid=202,
        x=100, y=100, width=1280, height=720,
        client_x=108, client_y=130, client_width=1264, client_height=682,
        is_minimized=False, is_focused=True, dpi_scale=1.0
    )
    wm.set_mock_window(mock_win)
    ctrl = InputController(wm, dry_run_default=True)

    # Convert norm coord to screen pixel
    sx, sy, win = ctrl._resolve_screen_coords(0.5, 0.5)
    assert sx == 108 + int(0.5 * 1264)
    assert sy == 130 + int(0.5 * 682)
    assert win.hwnd == 2002


@pytest.mark.asyncio
async def test_missing_or_invalid_baseline_fails_explicitly():
    """Defect 4: Missing or invalid pre_capture_id must produce explicit failure."""
    ctx = UGMContext(offline_fixture="tests/fixtures/hoi4_topbar.png")
    server = create_server(ctx)

    # Case A: Invalid / nonexistent pre_capture_id
    res_invalid = await server.call_tool("verify_state", {
        "pre_capture_id": "invalid_cap_id_nonexistent",
        "min_diff_ratio": 0.01
    })
    data_invalid = extract_data(res_invalid)
    assert data_invalid["verified"] is False
    assert "not found in cache" in data_invalid["message"]

    # Case B: Missing pre_capture_id when visual diff is required
    res_missing = await server.call_tool("verify_state", {
        "pre_capture_id": None,
        "expected_text": None,
        "min_diff_ratio": 0.01
    })
    data_missing = extract_data(res_missing)
    assert data_missing["verified"] is False
    assert "no baseline" in data_missing["message"].lower()


def test_capture_fallback_chain():
    """Defect 5: Failing backend triggers viable fallback, not silent corrupt capture."""
    cap = CaptureManager(allow_fixture_fallback=False)
    # Inject failing backend
    cap.set_backend(FailingBackend())

    # When fixture fallback is disabled and all live fallbacks fail, must raise
    with pytest.raises(RuntimeError) as excinfo:
        cap.capture()
    assert "capture backends failed" in str(excinfo.value).lower()

    # When fixture fallback is allowed, gracefully fall back to fixture
    cap_allow = CaptureManager(allow_fixture_fallback=True)
    cap_allow.set_backend(FailingBackend())
    frame = cap_allow.capture()
    assert isinstance(frame, np.ndarray)
    assert isinstance(cap_allow.backend, FixtureBackend)


def test_anti_cheat_and_bounds_safety():
    """Defect 6: Anti-cheat blacklists and out-of-bounds coordinates are strictly rejected."""
    safety_manager.reset_emergency_stop()

    with pytest.raises(SafetyViolationError):
        safety_manager.validate_process("EasyAntiCheat.exe")

    # Anti-cheat triggers emergency stop
    assert safety_manager.is_stopped is True

    # Attempting input while emergency stop is active must raise
    wm = WindowManager()
    wm.set_mock_window(WindowInfo(
        hwnd=3003, title="Test", process_name="test.exe", pid=303,
        x=0, y=0, width=500, height=500,
        client_x=0, client_y=0, client_width=500, client_height=500,
        is_minimized=False, is_focused=True, dpi_scale=1.0
    ))
    ctrl = InputController(wm, dry_run_default=True)
    res = ctrl.click(0.5, 0.5)
    assert res.executed is False
    assert "Emergency stop is active" in res.reason

    with pytest.raises(EmergencyStopTriggered):
        ctrl.press_keys(["space"])

    safety_manager.reset_emergency_stop()

    # Out of bounds coordinates rejected
    res_oob1 = ctrl.click(-0.1, 0.5)
    assert res_oob1.executed is False
    assert "rejected" in res_oob1.reason.lower()

    res_oob2 = ctrl.click(0.5, 1.2)
    assert res_oob2.executed is False
    assert "rejected" in res_oob2.reason.lower()
