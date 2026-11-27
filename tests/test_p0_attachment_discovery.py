"""P0 window/affordance regressions (synthetic; no real game or inputs)."""

from pathlib import Path

import numpy as np
import pytest

from ugm.core.capture import CaptureManager, FixtureBackend
from ugm.core.ocr import MockOCREngine
from ugm.core.types import NormalizedRect
from ugm.server import UGMContext, create_server

FIXTURE = Path(__file__).parent / "fixtures" / "sim_fixture.png"


@pytest.fixture(autouse=True)
def mocked_ocr(monkeypatch):
    monkeypatch.setattr("ugm.server.get_ocr_engine", lambda **_: MockOCREngine())


@pytest.fixture
def live_ctx(monkeypatch, tmp_path):
    # Avoid hardware capture initialization while exercising live attachment.
    monkeypatch.setattr(CaptureManager, "_init_preferred_backend", lambda *a: FixtureBackend())
    return UGMContext(db_path=tmp_path / "live.db", skills_pack_dir=None)


@pytest.fixture
def fixture_ctx(tmp_path):
    return UGMContext(
        offline_fixture=str(FIXTURE),
        fixture_title="Unfamiliar Game",
        fixture_process_name="unfamiliar.exe",
        db_path=tmp_path / "test.db",
        skills_pack_dir=None,
    )


@pytest.mark.asyncio
async def test_live_discovery_cannot_attach_or_register_an_unmatched_window(live_ctx, monkeypatch):
    monkeypatch.setattr(live_ctx.window_manager, "find_window", lambda **_: None)
    result = await create_server(live_ctx).call_tool("discover_and_attach_game", {
        "window_title": "Missing Game", "process_name": "missing_game.exe",
    })
    payload = result.structured_content.get("result", result.structured_content)
    assert payload["attached"] is False and payload["window"] is None
    assert live_ctx.window_manager.current_window is None
    assert live_ctx.storage.get_game("missing_game") is None
    assert "missing_game" not in live_ctx.navigation_graphs


def test_fixture_discovery_uses_only_the_configured_window(fixture_ctx):
    missing = fixture_ctx.discover_and_attach(window_title="Another Game", process_name="another.exe")
    assert missing["attached"] is False
    assert fixture_ctx.storage.get_game("another") is None
    suffix_match = fixture_ctx.discover_and_attach(process_name="familiar.exe")
    assert suffix_match["attached"] is False
    explicit = fixture_ctx.discover_and_attach(
        window_title="Unfamiliar", process_name="Unfamiliar.EXE",
    )
    foreground = fixture_ctx.discover_and_attach()
    assert explicit["attached"] and foreground["attached"]
    assert explicit["game_id"] == foreground["game_id"] == "unfamiliar"
    assert explicit["window"] == foreground["window"]


def test_empty_live_foreground_does_not_borrow_previous_window(live_ctx, fixture_ctx, monkeypatch):
    wm = live_ctx.window_manager
    monkeypatch.setattr(wm, "get_foreground_window", lambda: None, raising=False)
    wm.attach_window(fixture_ctx.window_manager.current_window)
    result = live_ctx.discover_and_attach()
    assert result["attached"] is False
    assert live_ctx.storage.get_game("unfamiliar") is None


@pytest.mark.parametrize("level", [0, 255])
def test_blank_roi_does_not_propose_a_center_click(fixture_ctx, level):
    explorer = fixture_ctx.explorer
    frame = np.full((220, 340, 3), level, dtype=np.uint8)
    # ROI sized like a button must not be recognized purely from its dimensions.
    bbox = NormalizedRect(0.2, 0.2, 0.4, 0.25)
    for mode in ("all", "shapes", "buttons"):
        assert explorer.discover_interactive_elements(frame, roi=bbox, scan_mode=mode) == []


def test_contour_candidate_in_roi_comes_from_shape(fixture_ctx):
    explorer = fixture_ctx.explorer
    frame = np.zeros((200, 300, 3), dtype=np.uint8)
    frame[100:130, 135:225] = 255
    bbox = NormalizedRect(0.3, 0.4, 0.6, 0.4)
    candidates = explorer.discover_interactive_elements(frame, roi=bbox, scan_mode="shapes")
    assert len(candidates) == 1
    assert candidates[0].details["source"] == "contour"
    assert candidates[0].center == pytest.approx((0.6, 0.575))


def test_live_foreground_is_queried_and_passes_process_safety(live_ctx, fixture_ctx, monkeypatch):
    import sys
    from types import SimpleNamespace

    from ugm.core.safety import SafetyViolationError, safety_manager

    wm = live_ctx.window_manager
    observed = fixture_ctx.window_manager.current_window
    monkeypatch.setattr("ugm.core.window.sys.platform", "win32")
    monkeypatch.setitem(sys.modules, "win32gui", SimpleNamespace(
        GetForegroundWindow=lambda: observed.hwnd,
        IsWindow=lambda hwnd: hwnd == observed.hwnd,
        IsWindowVisible=lambda hwnd: hwnd == observed.hwnd,
    ))
    monkeypatch.setattr(wm, "get_window_info", lambda hwnd: observed)

    def block(process_name):
        assert process_name == observed.process_name
        raise SafetyViolationError("blocked by test")

    monkeypatch.setattr(safety_manager, "validate_process", block)
    with pytest.raises(SafetyViolationError, match="blocked by test"):
        live_ctx.discover_and_attach()
    assert wm.current_window is None
    monkeypatch.setattr(safety_manager, "validate_process", lambda *_: None)
    attached = live_ctx.discover_and_attach()
    assert attached["attached"] and attached["window"]["hwnd"] == observed.hwnd


def test_live_lookup_revalidates_identity_after_native_enumeration(live_ctx, fixture_ctx, monkeypatch):
    import sys
    from dataclasses import replace
    from types import SimpleNamespace

    wm = live_ctx.window_manager
    observed = fixture_ctx.window_manager.current_window
    hwnd = observed.hwnd
    monkeypatch.setattr("ugm.core.window.sys.platform", "win32")
    monkeypatch.setitem(sys.modules, "win32gui", SimpleNamespace(
        EnumWindows=lambda callback, extra: callback(hwnd, extra),
        IsWindowVisible=lambda _: True,
        GetWindowText=lambda _: observed.title,
    ))
    monkeypatch.setitem(sys.modules, "win32process", SimpleNamespace(
        GetWindowThreadProcessId=lambda _: (10, observed.pid),
    ))
    monkeypatch.setattr("psutil.Process", lambda _: SimpleNamespace(name=lambda: observed.process_name))
    monkeypatch.setattr(wm, "get_window_info", lambda *_: replace(observed, pid=observed.pid + 1))
    result = live_ctx.discover_and_attach(process_name=observed.process_name)
    assert result["attached"] is False
    assert wm.current_window is None


@pytest.mark.parametrize("roi", [
    NormalizedRect(-0.1, 0, 0.3, 0.3),
    NormalizedRect(0.1, 0, 0, 0.3),
    NormalizedRect(0.9, 0, 0.3, 0.3),
    NormalizedRect(0, float("nan"), 0.3, 0.3),
])
def test_invalid_roi_does_not_expand_scan(fixture_ctx, roi):
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    with pytest.raises(ValueError, match="Exploration ROI"):
        fixture_ctx.explorer.discover_interactive_elements(frame, roi=roi, scan_mode="shapes")


@pytest.mark.asyncio
async def test_malformed_mcp_roi_is_rejected_before_capture(fixture_ctx, monkeypatch):
    def should_not_capture(**_):
        pytest.fail("a malformed ROI must not turn into a full-frame scan")

    monkeypatch.setattr(fixture_ctx.capture_manager, "capture", should_not_capture)
    with pytest.raises(ValueError, match="Exploration ROI"):
        await create_server(fixture_ctx).call_tool("explore_interactive_elements", {"roi": [0.5, 0.5]})
