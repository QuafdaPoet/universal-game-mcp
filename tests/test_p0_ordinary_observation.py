"""MCP capture/OCR attachment checks with a fully synthetic native backend."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from ugm.core.capture import BaseCaptureBackend, CaptureManager, FixtureBackend
from ugm.core.safety import SafetyViolationError
from ugm.core.types import WindowInfo
from ugm.server import UGMContext, create_server

TOOLS = ["capture_screen", "read_text"]
FIXTURE = Path(__file__).parent / "fixtures" / "sim_fixture.png"
WINDOW = WindowInfo(
    hwnd=600, title="Offline test window", process_name="offline_test.exe", pid=234,
    x=10, y=20, width=80, height=60,
    client_x=10, client_y=20, client_width=80, client_height=60,
    is_minimized=False, is_focused=True,
)


@pytest.fixture(params=[False, True], ids=["live", "dry-run"])
def native_ctx(request, tmp_path, monkeypatch):
    """No native backend or Win32 window lookup can run in these regressions."""
    class Backend(BaseCaptureBackend):
        def capture(self, **kwargs):
            pytest.fail("Rejected native observation should not reach the capture backend.")

    monkeypatch.setattr(CaptureManager, "_init_preferred_backend", lambda self: Backend())
    ctx = UGMContext(
        db_path=tmp_path / "test.db", ocr_backend="mock", skills_pack_dir=None,
        enforce_dry_run=request.param,
    )
    return ctx


def prevent_outputs(ctx, monkeypatch):
    def should_not_run(*args, **kwargs):
        pytest.fail("Rejected capture reached OCR, image output, or a filesystem save")

    for method in ("encode_base64_png", "save_image"):
        monkeypatch.setattr(ctx.capture_manager, method, should_not_run)
    monkeypatch.setattr(ctx.ocr_engine, "read_text", should_not_run)


def args_for(tool):
    return {"save_path": "must-not-save.png"} if tool == "capture_screen" else {}


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", [*TOOLS, "get_ui_state"])
@pytest.mark.parametrize("state", [{"is_focused": False}, {"is_minimized": True}], ids=["blurred", "minimized"])
async def test_invalid_native_window_blocks_capture_before_backend(native_ctx, monkeypatch, tool, state):
    native_ctx.window_manager.set_mock_window(replace(WINDOW, **state))
    prevent_outputs(native_ctx, monkeypatch)
    with pytest.raises(SafetyViolationError, match="target"):
        await create_server(native_ctx).call_tool(tool, args_for(tool))
    assert native_ctx.pre_capture_cache == {}
    assert native_ctx.capture_manager.last_frame is None


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", TOOLS)
@pytest.mark.parametrize("change", [
    {"pid": WINDOW.pid + 1}, {"is_focused": False}, {"client_x": WINDOW.client_x + 1}
], ids=["identity", "focus", "geometry"])
async def test_native_frame_is_discarded_when_target_changes_during_grab(native_ctx, monkeypatch, tool, change):
    native_ctx.window_manager.set_mock_window(WINDOW)
    prevent_outputs(native_ctx, monkeypatch)

    def grab(window, roi):
        assert window == WINDOW
        native_ctx.window_manager.set_mock_window(replace(WINDOW, **change))
        return np.zeros((WINDOW.client_height, WINDOW.client_width, 3), dtype=np.uint8)

    monkeypatch.setattr(native_ctx.capture_manager.backend, "capture", grab)
    with pytest.raises(SafetyViolationError, match="target"):
        await create_server(native_ctx).call_tool(tool, args_for(tool))
    assert native_ctx.pre_capture_cache == {}
    assert native_ctx.capture_manager.last_frame is None


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", [*TOOLS, "get_ui_state"])
async def test_native_observation_cannot_accept_an_unconfigured_fixture(native_ctx, monkeypatch, tool):
    native_ctx.window_manager.set_mock_window(WINDOW)
    assert not native_ctx.capture_manager.allow_fixture_fallback
    native_ctx.capture_manager.set_backend(FixtureBackend(np.zeros(
        (WINDOW.client_height, WINDOW.client_width, 3), dtype=np.uint8,
    )))
    prevent_outputs(native_ctx, monkeypatch)
    with pytest.raises(SafetyViolationError, match="fixture"):
        await create_server(native_ctx).call_tool(tool, args_for(tool))
    assert native_ctx.pre_capture_cache == {}
    assert native_ctx.capture_manager.last_frame is None


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", [*TOOLS, "get_ui_state"])
async def test_native_observation_refuses_wrong_client_frame_size(native_ctx, monkeypatch, tool):
    native_ctx.window_manager.set_mock_window(WINDOW)
    monkeypatch.setattr(native_ctx.capture_manager.backend, "capture", lambda **_: np.zeros(
        (WINDOW.client_height + 1, WINDOW.client_width, 3), dtype=np.uint8,
    ))
    prevent_outputs(native_ctx, monkeypatch)
    with pytest.raises(SafetyViolationError, match="does not match"):
        await create_server(native_ctx).call_tool(tool, args_for(tool))
    assert native_ctx.pre_capture_cache == {}
    assert native_ctx.capture_manager.last_frame is None


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", TOOLS)
async def test_native_observation_remains_available_with_focused_attachment(native_ctx, monkeypatch, tool):
    native_ctx.window_manager.set_mock_window(WINDOW)
    calls = []

    def grab(window, roi):
        calls.append(window)
        return np.zeros((WINDOW.client_height, WINDOW.client_width, 3), dtype=np.uint8)

    monkeypatch.setattr(native_ctx.capture_manager.backend, "capture", grab)
    result = await create_server(native_ctx).call_tool(tool, {"roi": [0.0, 0.0, 0.5, 0.5]})
    assert not result.is_error
    assert calls == [WINDOW]
    payload = result.structured_content.get("result", result.structured_content)
    if tool == "capture_screen":
        assert (payload["width"], payload["height"]) == (40, 30)
        assert native_ctx.pre_capture_cache[payload["capture_id"]].frame.shape == (60, 80, 3)
    else:
        assert payload["engine"] == "MockOCR"


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", TOOLS)
async def test_explicit_fixture_observation_does_not_require_native_focus(tmp_path, tool):
    ctx = UGMContext(
        offline_fixture=str(FIXTURE), db_path=tmp_path / "test.db",
        ocr_backend="mock", skills_pack_dir=None,
    )
    ctx.window_manager.set_mock_window(replace(ctx.window_manager.current_window, is_focused=False))
    result = await create_server(ctx).call_tool(tool, {})
    assert not result.is_error


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", [*TOOLS, "get_ui_state"])
async def test_fixture_observation_never_runs_an_accidentally_native_backend(tmp_path, monkeypatch, tool):
    ctx = UGMContext(
        offline_fixture=str(FIXTURE), db_path=tmp_path / "test.db",
        ocr_backend="mock", skills_pack_dir=None,
    )

    class Backend(BaseCaptureBackend):
        def capture(self, **kwargs):
            pytest.fail("Offline fixture session reached a native capture backend.")

    ctx.capture_manager.set_backend(Backend())
    prevent_outputs(ctx, monkeypatch)
    with pytest.raises(SafetyViolationError, match="fixture"):
        await create_server(ctx).call_tool(tool, args_for(tool))


@pytest.mark.asyncio
async def test_fixture_switch_invalidates_native_dry_run_and_fixture_baselines(tmp_path, monkeypatch):
    class Backend(BaseCaptureBackend):
        def capture(self, **kwargs):
            return np.zeros((WINDOW.client_height, WINDOW.client_width, 3), dtype=np.uint8)

    monkeypatch.setattr(CaptureManager, "_init_preferred_backend", lambda self: Backend())
    ctx = UGMContext(
        db_path=tmp_path / "test.db", ocr_backend="mock", skills_pack_dir=None,
        enforce_dry_run=True,
    )
    server = create_server(ctx)
    ctx.window_manager.set_mock_window(WINDOW)

    for title in ("First Fixture", "Second Fixture"):
        result = await server.call_tool("capture_screen", {})
        payload = result.structured_content.get("result", result.structured_content)
        assert payload["capture_id"] in ctx.pre_capture_cache
        ctx.setup_offline_fixture(str(FIXTURE), title=title)
        assert ctx.pre_capture_cache == {}


def prevent_recognition(ctx, monkeypatch):
    def should_not_run(*args, **kwargs):
        pytest.fail("Rejected frame reached state recognition or the knowledge store")

    monkeypatch.setattr(ctx.state_recognizer, "recognize_state", should_not_run)
    for method in ("find_matching_state", "record_ui_state"):
        monkeypatch.setattr(ctx.storage, method, should_not_run)


@pytest.mark.asyncio
async def test_ui_state_requires_a_native_attachment(native_ctx, monkeypatch):
    prevent_recognition(native_ctx, monkeypatch)
    with pytest.raises(SafetyViolationError, match="No target game window attached"):
        await create_server(native_ctx).call_tool("get_ui_state", {})


@pytest.mark.asyncio
async def test_ui_state_rejects_a_different_game_before_capture(native_ctx, monkeypatch):
    native_ctx.window_manager.set_mock_window(WINDOW)
    prevent_recognition(native_ctx, monkeypatch)
    with pytest.raises(SafetyViolationError, match="does not match"):
        await create_server(native_ctx).call_tool("get_ui_state", {"game_id": "other_game"})


@pytest.mark.asyncio
@pytest.mark.parametrize("game_id", [None, "offline_test"], ids=["derive-from-window", "matching-id"])
async def test_ui_state_recognizes_only_the_captured_game(native_ctx, monkeypatch, game_id):
    native_ctx.window_manager.set_mock_window(WINDOW)
    assert native_ctx.active_game_id != "offline_test"
    frame = np.zeros((WINDOW.client_height, WINDOW.client_width, 3), dtype=np.uint8)

    def grab(window, roi):
        assert window == WINDOW
        return frame

    def recognize(image, *, game_id, hint_state, simulation, record_observation):
        assert image is frame
        assert game_id == "offline_test"
        assert hint_state == "main_view"
        assert simulation is native_ctx.simulation_session
        assert record_observation is True
        return SimpleNamespace(to_dict=lambda: {"state_name": "main_view"})

    monkeypatch.setattr(native_ctx.capture_manager.backend, "capture", grab)
    monkeypatch.setattr(native_ctx.state_recognizer, "recognize_state", recognize)
    args = {"hint_state": "main_view"}
    if game_id is not None:
        args["game_id"] = game_id
    result = await create_server(native_ctx).call_tool("get_ui_state", args)
    assert not result.is_error


@pytest.mark.asyncio
async def test_ui_state_discards_native_frame_if_target_changes(native_ctx, monkeypatch):
    native_ctx.window_manager.set_mock_window(WINDOW)
    prevent_recognition(native_ctx, monkeypatch)

    def grab(window, roi):
        assert window == WINDOW
        native_ctx.window_manager.set_mock_window(replace(WINDOW, process_name="other.exe"))
        return np.zeros((WINDOW.client_height, WINDOW.client_width, 3), dtype=np.uint8)

    monkeypatch.setattr(native_ctx.capture_manager.backend, "capture", grab)
    with pytest.raises(SafetyViolationError, match="target"):
        await create_server(native_ctx).call_tool("get_ui_state", {})
    assert native_ctx.capture_manager.last_frame is None


@pytest.mark.asyncio
async def test_ui_state_keeps_explicit_fixture_id_in_simulation(tmp_path, monkeypatch):
    ctx = UGMContext(
        offline_fixture=str(FIXTURE), db_path=tmp_path / "test.db",
        ocr_backend="mock", skills_pack_dir=None,
    )
    ctx.window_manager.set_mock_window(replace(ctx.window_manager.current_window, is_focused=False))

    def recognize(frame, *, game_id, hint_state, simulation, record_observation):
        assert frame.size
        assert game_id == "fixture_scenario"
        assert simulation is True
        assert record_observation is True
        return SimpleNamespace(to_dict=lambda: {"state_name": "fixture"})

    monkeypatch.setattr(ctx.state_recognizer, "recognize_state", recognize)
    result = await create_server(ctx).call_tool("get_ui_state", {"game_id": "fixture_scenario"})
    assert not result.is_error


async def call_observation(ctx, tool, args):
    result = await create_server(ctx).call_tool(tool, args)
    assert not result.is_error
    return result.structured_content.get("result", result.structured_content)


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["verify_state", "verify_selection"])
async def test_invalid_baseline_does_not_capture(native_ctx, monkeypatch, tool):
    native_ctx.window_manager.set_mock_window(WINDOW)
    prevent_outputs(native_ctx, monkeypatch)
    result = await call_observation(native_ctx, tool, {
        "pre_capture_id": "missing", "expected_text": "Research",
    })
    assert not result["verified"]
    assert result["details"]["error"] == "invalid_pre_capture_id"


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["swap", "round_trip", "geometry", "expired", "backend"])
async def test_stale_baseline_is_rejected_before_capture_and_ocr(native_ctx, monkeypatch, change):
    import ugm.server as server_module
    ctx = native_ctx
    ctx.window_manager.set_mock_window(WINDOW)
    backend = ctx.capture_manager.backend
    reject_grab = backend.capture
    frame = np.zeros((WINDOW.client_height, WINDOW.client_width, 3), dtype=np.uint8)
    monkeypatch.setattr(backend, "capture", lambda **_: frame)
    first = await call_observation(ctx, "capture_screen", {})
    monkeypatch.setattr(backend, "capture", reject_grab)
    prevent_outputs(ctx, monkeypatch)

    if change in {"swap", "round_trip"}:
        other = replace(WINDOW, hwnd=WINDOW.hwnd + 1, pid=WINDOW.pid + 1, process_name="other.exe")
        ctx.window_manager.attach_window(other)
        if change == "round_trip":
            ctx.window_manager.attach_window(WINDOW)
    elif change == "geometry":
        # Simulate an in-place native refresh, not an explicit attach.
        moved = replace(WINDOW, client_x=WINDOW.client_x + 1)
        monkeypatch.setattr(type(ctx.window_manager), "current_window", property(lambda _: moved))
    elif change == "expired":
        now = server_module.time.monotonic()
        monkeypatch.setattr(server_module, "_baseline_clock", lambda: now + 300)
    else:
        ctx.capture_manager.set_backend(type(backend)())

    result = await call_observation(ctx, "verify_state", {
        "pre_capture_id": first["capture_id"], "expected_text": "Research",
    })
    assert result["verified"] is False
    assert result["details"]["error"] == "invalid_pre_capture_id"


@pytest.mark.asyncio
async def test_fresh_native_baseline_and_text_only_still_work(native_ctx, monkeypatch):
    ctx = native_ctx
    ctx.window_manager.set_mock_window(WINDOW)
    dark = np.zeros((WINDOW.client_height, WINDOW.client_width, 3), dtype=np.uint8)
    frames = iter((dark, np.full_like(dark, 255), dark))
    monkeypatch.setattr(ctx.capture_manager.backend, "capture", lambda **_: next(frames))
    first = await call_observation(ctx, "capture_screen", {"roi": [0.0, 0.0, 0.5, 0.5]})
    result = await call_observation(ctx, "verify_selection", {"pre_capture_id": first["capture_id"]})
    assert result["verified"] and result["diff_ratio"] == 1
    monkeypatch.setattr(ctx.ocr_engine, "read_text", lambda *args, **kwargs: SimpleNamespace(full_text="Research"))
    text_result = await call_observation(ctx, "verify_state", {"expected_text": "Research"})
    assert text_result["verified"]
    assert text_result["evidence_level"] == ("simulated" if ctx.simulation_session else "verified")


@pytest.mark.asyncio
@pytest.mark.parametrize("attach_tool", ["get_game_window", "discover_and_attach_game"])
async def test_public_selection_invalidates_prior_baseline(native_ctx, monkeypatch, attach_tool):
    ctx = native_ctx
    ctx.window_manager.set_mock_window(WINDOW)
    backend = ctx.capture_manager.backend
    reject_grab = backend.capture
    frame = np.zeros((WINDOW.client_height, WINDOW.client_width, 3), dtype=np.uint8)
    monkeypatch.setattr(backend, "capture", lambda **_: frame)
    first = await call_observation(ctx, "capture_screen", {})
    attached = await call_observation(ctx, attach_tool, {"process_name": WINDOW.process_name, "window_title": WINDOW.title})
    assert attached.get("found") or attached.get("attached")
    monkeypatch.setattr(backend, "capture", reject_grab)
    result = await call_observation(ctx, "verify_state", {"pre_capture_id": first["capture_id"]})
    assert result["details"]["error"] == "invalid_pre_capture_id"


@pytest.mark.asyncio
async def test_verify_rejects_attachment_round_trip_during_grab(native_ctx, monkeypatch):
    ctx = native_ctx
    ctx.window_manager.set_mock_window(WINDOW)
    frame = np.zeros((WINDOW.client_height, WINDOW.client_width, 3), dtype=np.uint8)
    monkeypatch.setattr(ctx.capture_manager.backend, "capture", lambda **_: frame)
    first = await call_observation(ctx, "capture_screen", {})

    def grab(**kwargs):
        ctx.window_manager.attach_window(replace(WINDOW, hwnd=WINDOW.hwnd + 1))
        ctx.window_manager.attach_window(WINDOW)
        return np.full_like(frame, 255)

    monkeypatch.setattr(ctx.capture_manager.backend, "capture", grab)
    prevent_outputs(ctx, monkeypatch)
    result = await call_observation(ctx, "verify_selection", {
        "pre_capture_id": first["capture_id"], "expected_text": "Research",
    })
    assert result["details"]["error"] == "invalid_pre_capture_id"


@pytest.mark.asyncio
@pytest.mark.parametrize("width,height,retained", [(1920, 1080, 5), (3840, 2160, 1)])
async def test_public_capture_cache_bounds_full_frame_bytes(native_ctx, monkeypatch, width, height, retained):
    window = replace(WINDOW, width=width, height=height, client_width=width, client_height=height)
    native_ctx.window_manager.set_mock_window(window)
    fills = iter((5, 10, 15, 20, 25, 30, 255))
    monkeypatch.setattr(native_ctx.capture_manager.backend, "capture",
                        lambda **_: np.full((height, width, 3), next(fills), dtype=np.uint8))
    results = [await call_observation(native_ctx, "capture_screen", {"roi": [0, 0, 0.01, 0.01]})
               for _ in range(6)]
    cache = native_ctx.pre_capture_cache
    assert len(cache) == retained
    assert sum(item.frame.nbytes for item in cache.values()) <= 32 * 1024**2
    assert list(cache) == [item["capture_id"] for item in results[-retained:]]
    result = await call_observation(native_ctx, "verify_state", {
        "pre_capture_id": results[-1]["capture_id"],
        "roi": [0.5, 0.5, 0.01, 0.01],
    })
    assert result["verified"] and result["diff_ratio"] == 1


@pytest.mark.asyncio
async def test_expired_buffers_pruned_on_insert_and_lookup(native_ctx, monkeypatch):
    import ugm.server as server_module
    now = 0.0
    monkeypatch.setattr(server_module, "_baseline_clock", lambda: now)
    native_ctx.window_manager.set_mock_window(WINDOW)
    monkeypatch.setattr(native_ctx.capture_manager.backend, "capture",
                        lambda **_: np.zeros((60, 80, 3), dtype=np.uint8))
    first = await call_observation(native_ctx, "capture_screen", {})
    now = 61.0
    second = await call_observation(native_ctx, "capture_screen", {})
    assert first["capture_id"] not in native_ctx.pre_capture_cache
    now = 122.0
    invalid = await call_observation(native_ctx, "verify_state", {"pre_capture_id": second["capture_id"]})
    assert invalid["details"]["error"] == "invalid_pre_capture_id"
    assert native_ctx.pre_capture_cache == {}


@pytest.mark.asyncio
async def test_oversize_frame_still_returns_preview_without_retaining_baseline(native_ctx, monkeypatch):
    import ugm.server as server_module
    monkeypatch.setattr(server_module, "BASELINE_MAX_BYTES", 100)
    native_ctx.window_manager.set_mock_window(WINDOW)
    monkeypatch.setattr(native_ctx.capture_manager.backend, "capture",
                        lambda **_: np.zeros((60, 80, 3), dtype=np.uint8))
    capture = await call_observation(native_ctx, "capture_screen", {"roi": [0, 0, 0.5, 0.5]})
    assert capture["width"] == 40 and capture["image_base64"]
    assert capture["verification_baseline_available"] is False
    assert native_ctx.pre_capture_cache == {}
