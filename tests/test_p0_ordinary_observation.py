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
        assert native_ctx.pre_capture_cache[payload["capture_id"]].shape == (60, 80, 3)
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
