"""MCP action sequences are single-flight; stopping never waits for their lock."""
import asyncio
from threading import Event

import pytest

from ugm.core.safety import SafetyViolationError, safety_manager
from ugm.server import UGMContext, create_server


@pytest.mark.asyncio
async def test_parallel_tools_cannot_interleave_or_reattach(tmp_path, monkeypatch):
    entered, release = Event(), Event()
    ctx = UGMContext(db_path=tmp_path / "serial.db", ocr_backend="mock",
                     skills_pack_dir=None, offline_fixture="tests/fixtures/hoi4_topbar.png")
    server = create_server(ctx)
    safety_manager.reset_emergency_stop()

    def wait_probe(**kwargs):
        entered.set()
        assert release.wait(6), "Test did not release probe"
        return {"finished": True}

    def unexpected(*args, **kwargs):
        pytest.fail("Concurrent action or reattach reached the implementation")

    monkeypatch.setattr(ctx.explorer, "probe_step", wait_probe)
    monkeypatch.setattr(ctx.input_controller, "press_keys", unexpected)
    monkeypatch.setattr(ctx, "discover_and_attach", unexpected)
    running = asyncio.create_task(server.call_tool("probe_action", {"action": "hover"}))
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        for name, args in [
            ("press_keys", {"keys": ["space"]}),
            ("discover_and_attach_game", {}),
        ]:
            with pytest.raises(SafetyViolationError, match="in progress"):
                await asyncio.wait_for(server.call_tool(name, args), 1.5)

        stopped = await asyncio.wait_for(server.call_tool("emergency_stop", {"reason": "lease test"}), 1.5)
        assert not stopped.is_error and safety_manager.is_stopped
        assert not running.done()
    finally:
        release.set()
        try:
            await asyncio.wait_for(running, 6)
        finally:
            safety_manager.reset_emergency_stop()
            ctx.storage.db.close()


@pytest.mark.asyncio
async def test_action_exception_releases_lease(tmp_path, monkeypatch):
    ctx = UGMContext(db_path=tmp_path / "serial.db", ocr_backend="mock", skills_pack_dir=None)
    server = create_server(ctx)

    def fail_probe(**kwargs):
        raise ValueError("test action failed")

    monkeypatch.setattr(ctx.explorer, "probe_step", fail_probe)
    try:
        with pytest.raises(ValueError, match="test action failed"):
            await server.call_tool("probe_action", {"action": "hover"})
        monkeypatch.setattr(ctx.explorer, "probe_step", lambda **kwargs: {"finished": True})
        assert not (await server.call_tool("probe_action", {"action": "hover"})).is_error
    finally:
        ctx.storage.db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "arg"), [
    ("navigate_to", "target_state"),
    ("execute_skill", "skill_id"),
])
async def test_game_specific_action_cannot_use_an_unrelated_window(tmp_path, monkeypatch, name, arg):
    from ugm.core.types import WindowInfo

    ctx = UGMContext(db_path=tmp_path / "actions.db", ocr_backend="mock", skills_pack_dir=None)
    ctx.window_manager.set_mock_window(
        WindowInfo(1, "Offline Game", "testgame.exe", 42, 0, 0, 80, 60, 0, 0, 80, 60, False, True),
    )
    def unexpected(*args, **kwargs):
        pytest.fail("An unrelated game's action reached the execution path")
    if name == "navigate_to":
        monkeypatch.setattr(ctx, "get_navigator", unexpected)
    else:
        monkeypatch.setattr(ctx.skill_engine, "execute_skill", unexpected)
    try:
        with pytest.raises(SafetyViolationError, match="game_id"):
            await create_server(ctx).call_tool(name, {arg: "test", "game_id": "othergame"})
    finally:
        ctx.storage.db.close()


@pytest.mark.asyncio
async def test_invalid_click_verification_region_does_not_dispatch(tmp_path, monkeypatch):
    ctx = UGMContext(db_path=tmp_path / "actions.db", ocr_backend="mock", skills_pack_dir=None)
    def unexpected(*args, **kwargs):
        pytest.fail("Invalid verification region reached click executor")
    monkeypatch.setattr(ctx.target_executor, "click_and_verify", unexpected)
    try:
        with pytest.raises(ValueError, match="Verification.*ROI"):
            await create_server(ctx).call_tool("click_target", {
                "x": .3, "y": .4, "verification_roi": [],
            })
    finally:
        ctx.storage.db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("verify", [False, True])
async def test_click_contract_does_not_misreport_verification(tmp_path, monkeypatch, verify):
    from dataclasses import replace

    import numpy as np

    from ugm.core.types import ClickResult, WindowInfo

    safety_manager.reset_emergency_stop()
    ctx = UGMContext(db_path=tmp_path / "bound.db", ocr_backend="mock", skills_pack_dir=None)
    window = WindowInfo(1, "Offline Game", "testgame.exe", 42, 0, 0, 80, 60, 0, 0, 80, 60, False, True)
    ctx.window_manager.set_mock_window(window)
    called = []
    def capture(manager, expected):
        assert manager is ctx.window_manager and expected == window
        called.append("capture")
        return np.zeros((60, 80, 3), dtype=np.uint8)
    def click(**kwargs):
        assert kwargs["target_window"] == window
        called.append("click")
        ctx.window_manager.set_mock_window(replace(window, hwnd=2))
        return ClickResult(.3, .4, 24, 24, "left", 1, False, True, False, "mock", .1)
    monkeypatch.setattr(ctx.capture_manager, "capture_attached_window", capture)
    monkeypatch.setattr(ctx.input_controller, "click", click)
    try:
        result = await create_server(ctx).call_tool("click_target", {"x": .3, "y": .4, "verify": verify})
        data = result.structured_content.get("result", result.structured_content)
        assert data["executed"] and not data["verification_passed"]
        assert data["evidence"]["evidence_level"] == "observed"
        assert called == (["capture", "click"] if verify else ["click"])
        assert data["evidence"]["verification_interrupted" if verify else "verification_skipped"]
    finally:
        ctx.storage.db.close()
