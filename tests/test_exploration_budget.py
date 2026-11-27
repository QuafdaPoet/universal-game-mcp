"""An opt-in goal session bounds actual capture attempts and fails closed."""
from dataclasses import replace

import pytest

from ugm.core.safety import SafetyViolationError
from ugm.server import UGMContext, create_server


@pytest.fixture
def fixture_context(tmp_path, monkeypatch):
    ctx = UGMContext(db_path=tmp_path / "budget.db", ocr_backend="mock",
                     skills_pack_dir=None, offline_fixture="tests/fixtures/hoi4_topbar.png")
    captures = []
    capture = ctx.capture_manager.capture
    def count(*args, **kw):
        captures.append(True)
        return capture(*args, **kw)
    monkeypatch.setattr(ctx.capture_manager, "capture", count)
    yield ctx, create_server(ctx), captures
    ctx.storage.db.close()


async def explore(server, **arguments):
    result = await server.call_tool("explore_interactive_elements", {"goal": "gold", **arguments})
    return result.structured_content.get("result", result.structured_content)


@pytest.mark.asyncio
async def test_quota_limits_capture_attempts_but_keeps_legacy_goal_preview(fixture_context):
    _, server, captures = fixture_context
    first = await explore(server, session_scan_budget=2)
    sid = first["session"]["id"]
    assert first["session"]["scans_remaining"] == 1
    second = await explore(server, exploration_session_id=sid)
    assert second["session"]["scans_remaining"] == 0
    with pytest.raises(ValueError, match="exhausted"):
        await explore(server, exploration_session_id=sid)
    assert len(captures) == 2
    ordinary = await explore(server)
    assert "session" not in ordinary and len(captures) == 3


@pytest.mark.asyncio
async def test_session_refuses_rebound_fixture_before_capture(fixture_context):
    ctx, server, captures = fixture_context
    first = await explore(server, session_scan_budget=3)
    win = ctx.window_manager.current_window
    ctx.window_manager.set_mock_window(replace(win))
    with pytest.raises(SafetyViolationError, match="scope changed"):
        await explore(server, exploration_session_id=first["session"]["id"])
    assert len(captures) == 1


@pytest.mark.asyncio
async def test_failed_continuation_charges_an_attempt(fixture_context, monkeypatch):
    ctx, server, captures = fixture_context
    first = await explore(server, session_scan_budget=2)
    sid = first["session"]["id"]
    with monkeypatch.context() as fail:
        def no_ocr(*a, **kw):
            raise RuntimeError("injected OCR failure")
        fail.setattr(ctx.explorer, "discover_interactive_elements", no_ocr)
        with pytest.raises(RuntimeError, match="injected OCR failure"):
            await explore(server, exploration_session_id=sid)
    with pytest.raises(ValueError, match="exhausted"):
        await explore(server, exploration_session_id=sid)
    assert len(captures) == 2


def test_budget_expiry_and_parallel_reservations(monkeypatch):
    from ugm.core import exploration_budget as module

    clock = [100.0]
    monkeypatch.setattr(module, "time", type("Clock", (), {"monotonic": lambda: clock[0]}))
    budget = module.ExplorationBudget()
    sid = budget.reserve("scope", None, 2)
    with pytest.raises(ValueError, match="already running"):
        budget.reserve("scope", sid, None)
    assert budget.finish(sid)["scans_remaining"] == 1
    clock[0] += module.SESSION_TTL_SECONDS
    with pytest.raises(ValueError, match="expired"):
        budget.reserve("scope", sid, None)


@pytest.mark.asyncio
async def test_invalid_parameters_do_not_start_capture(fixture_context):
    _, server, captures = fixture_context
    with pytest.raises(ValueError, match="not both"):
        await explore(server, session_scan_budget=2, exploration_session_id="a" * 32)
    with pytest.raises(ValueError, match="require a goal"):
        await server.call_tool("explore_interactive_elements", {"session_scan_budget": 2})
    assert captures == []
