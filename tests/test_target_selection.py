"""
Unit tests for TargetResolver, TargetExecutor, ambiguity rejection,
and post-click state verification.
"""

import numpy as np
import cv2
import pytest

from ugm.core.types import (
    NormalizedRect,
    BoundingBox,
    TargetStatus,
    TargetResolution,
    WindowInfo
)
from ugm.core.icon import IconDetector
from ugm.core.ocr import MockOCREngine, OCRItem
from ugm.core.capture import CaptureManager, FixtureBackend
from ugm.core.window import WindowManager
from ugm.core.input import InputController
from ugm.core.verifier import ActionVerifier
from ugm.core.target import TargetResolver, TargetExecutor
from ugm.core.safety import safety_manager


@pytest.fixture
def target_fixture_env():
    safety_manager.reset_emergency_stop()
    # Synthetic 1920x1080 frame
    frame = np.full((1080, 1920, 3), 40, dtype=np.uint8)

    # Draw template icon at (200, 200)
    icon_tmpl = np.zeros((30, 30, 3), dtype=np.uint8)
    cv2.circle(icon_tmpl, (15, 15), 10, (0, 200, 255), -1)
    frame[200:230, 200:230] = icon_tmpl

    window_mgr = WindowManager()
    mock_win = WindowInfo(
        hwnd=1001,
        title="Strategy Game Window",
        process_name="game.exe",
        pid=1234,
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
    window_mgr.set_mock_window(mock_win)

    fixture_backend = FixtureBackend(frame)
    cap_mgr = CaptureManager()
    cap_mgr.set_backend(fixture_backend)

    icon_det = IconDetector()
    icon_det.register_template("gold_coin", icon_tmpl)

    mock_ocr = MockOCREngine()
    input_ctrl = InputController(window_mgr, dry_run_default=True)
    verifier = ActionVerifier(cap_mgr, mock_ocr, input_ctrl)

    resolver = TargetResolver(icon_det, mock_ocr, verifier)
    executor = TargetExecutor(resolver, cap_mgr, input_ctrl, verifier)

    return {
        "frame": frame,
        "icon_tmpl": icon_tmpl,
        "cap_mgr": cap_mgr,
        "backend": fixture_backend,
        "resolver": resolver,
        "executor": executor,
        "mock_ocr": mock_ocr,
        "icon_det": icon_det
    }


def test_target_resolver_unambiguous_success(target_fixture_env):
    resolver = target_fixture_env["resolver"]
    frame = target_fixture_env["frame"]

    res = resolver.resolve_target(
        frame=frame,
        icon_name="gold_coin",
        threshold=0.85
    )

    assert res.status == "found"
    assert res.target_candidate is not None
    assert res.coordinates_normalized is not None
    cx, cy = res.coordinates_normalized
    # Expected near (215/1920, 215/1080)
    assert abs(cx - (215.0 / 1920.0)) < 0.02
    assert abs(cy - (215.0 / 1080.0)) < 0.02


def test_target_resolver_ambiguity_rejection(target_fixture_env):
    """When two identical or near-identical icons exist, reject as ambiguous."""
    frame = target_fixture_env["frame"].copy()
    icon_tmpl = target_fixture_env["icon_tmpl"]

    # Place a SECOND identical icon at (800, 200)
    frame[200:230, 800:830] = icon_tmpl

    resolver = target_fixture_env["resolver"]
    res = resolver.resolve_target(
        frame=frame,
        icon_name="gold_coin",
        threshold=0.85,
        score_tolerance=0.05
    )

    assert res.status == TargetStatus.AMBIGUOUS.value
    assert res.target_candidate is None
    assert len(res.all_candidates) >= 2
    assert res.escalation is not None
    assert res.escalation.needed is True
    assert "Ambiguous target" in res.escalation.reason


def test_target_executor_rejects_ambiguous_without_clicking(target_fixture_env):
    executor = target_fixture_env["executor"]

    # Provide an ambiguous resolution
    ambiguous_res = TargetResolution(
        status=TargetStatus.AMBIGUOUS.value,
        selector_id="gold_coin",
        reason="Two competing matches found"
    )

    click_res = executor.click_and_verify(
        target_resolution=ambiguous_res,
        dry_run=True
    )

    assert click_res.executed is False
    assert click_res.status == TargetStatus.AMBIGUOUS.value
    assert "Action rejected" in click_res.reason


def test_target_executor_verification_fails_when_no_visual_change(target_fixture_env):
    """
    Never report success based solely on whether a mouse click was sent!
    If screen does not change post-click, status must be 'failed'.
    """
    executor = target_fixture_env["executor"]
    cap_mgr = target_fixture_env["cap_mgr"]

    # Mock click execution to simulate hardware click dispatched without moving actual cursor
    from ugm.core.types import ClickResult
    def mock_click(norm_x, norm_y, button="left", clicks=1, dry_run=None):
        return ClickResult(
            x_normalized=norm_x,
            y_normalized=norm_y,
            x_pixel=int(norm_x * 1920),
            y_pixel=int(norm_y * 1080),
            button=button,
            clicks=clicks,
            dry_run=False,
            executed=True,
            verification_passed=None,
            reason="Mock hardware click dispatched.",
            latency_ms=1.0
        )
    executor.input_controller.click = mock_click

    # Live click dispatched, but verification fails because screen state does not change
    click_res = executor.click_and_verify(
        norm_x=0.5,
        norm_y=0.5,
        dry_run=False,
        min_diff_ratio=0.01  # requires 1% pixel diff
    )

    # Click was sent, but verification failed because screen did not react
    assert click_res.executed is True
    assert click_res.verification_passed is False
    assert click_res.status == TargetStatus.FAILED.value
    assert "verification failed" in click_res.reason


def test_target_executor_verification_succeeds_when_visual_change_detected(target_fixture_env):
    executor = target_fixture_env["executor"]
    backend = target_fixture_env["backend"]
    base_frame = target_fixture_env["frame"]

    # Mock click execution
    from ugm.core.types import ClickResult
    def mock_click(norm_x, norm_y, button="left", clicks=1, dry_run=None):
        return ClickResult(
            x_normalized=norm_x,
            y_normalized=norm_y,
            x_pixel=int(norm_x * 1920),
            y_pixel=int(norm_y * 1080),
            button=button,
            clicks=clicks,
            dry_run=False,
            executed=True,
            verification_passed=None,
            reason="Mock hardware click dispatched.",
            latency_ms=1.0
        )
    executor.input_controller.click = mock_click

    # Monkeypatch backend.capture to return a changed frame on 2nd call
    call_count = [0]
    def dynamic_capture(window=None, roi=None):
        call_count[0] += 1
        if call_count[0] == 1:
            return base_frame.copy()
        # 2nd call (post-action): add a large bright dialogue box
        changed = base_frame.copy()
        cv2.rectangle(changed, (400, 300), (1000, 700), (200, 200, 220), -1)
        return changed

    backend.capture = dynamic_capture

    click_res = executor.click_and_verify(
        norm_x=0.5,
        norm_y=0.5,
        dry_run=False,
        min_diff_ratio=0.01
    )

    assert click_res.executed is True
    assert click_res.verification_passed is True
    assert click_res.status == TargetStatus.SUCCESS.value
    assert "verified successfully" in click_res.reason
