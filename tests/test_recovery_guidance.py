from types import SimpleNamespace

import numpy as np
import pytest

from ugm.core.safety import SafetyViolationError
from ugm.core.types import WindowInfo
from ugm.server import UGMContext, create_server


@pytest.fixture
def native(tmp_path, monkeypatch):
    ctx = UGMContext(ocr_backend="mock", skills_pack_dir=None, db_path=tmp_path / "evidence.db")
    win = WindowInfo(1, "Game", "example.exe", 32, 0, 0, 80, 60, 0, 0, 80, 60, False, True)
    ctx.window_manager.set_mock_window(win)
    frames = []
    def capture(*args, **kwargs):
        frames.append(True)
        return np.zeros((60, 80, 3), dtype=np.uint8)
    monkeypatch.setattr(ctx.capture_manager, "capture_attached_window", capture)
    ctx.ocr_engine = SimpleNamespace()
    def classify(frame, *, game_id, simulation=False, record_observation):
        assert game_id == "example" and not simulation and not record_observation
        return SimpleNamespace(status="known", state_name="main", confidence=.94)
    monkeypatch.setattr(ctx.state_recognizer, "recognize_state", classify)
    for action in ("click", "hover", "press_keys"):
        monkeypatch.setattr(ctx.input_controller, action, lambda *a, **kw: pytest.fail("no input"))
    yield ctx, create_server(ctx), frames
    ctx.storage.db.close()


def seed(ctx, destination, *, producer="scoped_native_navigation", source="live", game_id="example",
         evidence_level="verified", outgoing="main", status="verified", repeats=2):
    for _ in range(repeats):
        ctx.storage.record_transition(game_id, outgoing, destination, "press_keys", {"keys": ["space"]},
            evidence={"producer":producer, "evidence_level":evidence_level, "observed_state":destination},
            source=source, status=status)


async def get(server, **kwargs):
    r = await server.call_tool("get_recovery_guidance", kwargs)
    return r.structured_content.get("result", r.structured_content)


@pytest.mark.asyncio
async def test_only_durable_verified_bound_native_edges_are_suggested(native):
    ctx, server, frames = native
    seed(ctx, "menu")
    seed(ctx, "reject")
    seed(ctx, "other", game_id="unrelated")
    seed(ctx, "unenclosed", producer="")
    seed(ctx, "simulated", source="synthetic_fixture")
    seed(ctx, "recent_failure", evidence_level="observed")
    seed(ctx, "once", repeats=1)
    seed(ctx, "candidate", status="candidate")
    ctx.storage.quarantine_route("example", "main", "reject", "press_keys", {"keys": ["space"]})
    result = await get(server)
    assert result["status"] == "candidate_destinations" and result["destinations"] == ["menu"]
    assert result["action_suggested"] is False and len(frames) == 2
    assert not any(value in repr(result) for value in ("press_keys", "coordinates", "space"))


@pytest.mark.asyncio
@pytest.mark.parametrize("second", ["other", "unknown", "visual"])
async def test_confirmed_current_state_requires_two_agreeing_fresh_frames(native, monkeypatch, second):
    ctx, server, frames = native
    seed(ctx, "menu")
    classify = ctx.state_recognizer.recognize_state
    def observe(frame, **kwargs):
        result = classify(frame, **kwargs)
        if len(frames) == 2:
            if second == "unknown":
                result.status = "ambiguous"
            elif second == "other":
                result.state_name = "another"
            else:
                frame.fill(200)
        return result
    monkeypatch.setattr(ctx.state_recognizer, "recognize_state", observe)
    result = await get(server)
    assert result["status"] == "insufficient_state_evidence" and result["destinations"] == []


@pytest.mark.asyncio
async def test_dry_run_cannot_get_live_guidance(native):
    ctx, server, frames = native
    ctx.input_controller.dry_run_default = True
    with pytest.raises(SafetyViolationError, match="dry.run"):
        await get(server)
    assert frames == []


@pytest.mark.asyncio
@pytest.mark.parametrize("mock_ocr", [False, True])
async def test_only_current_scoped_native_navigation_contributes_proof(native, monkeypatch, mock_ocr):
    from ugm.core.ocr import MockOCREngine
    from ugm.skills.contracts import NavigationEdge

    ctx, server, frames = native
    if mock_ocr:
        ctx.ocr_engine = MockOCREngine()
    graph = ctx.get_navigation_graph("example")
    graph.register_edge(NavigationEdge("main", "menu", "press_keys", {"keys": ["space"]}), persist=False)
    classify = ctx.state_recognizer.recognize_state
    def observe(frame, **kwargs):
        result = classify(frame, **kwargs)
        if len(frames) % 2 == 0:
            result.state_name = "menu"
        return result
    monkeypatch.setattr(ctx.state_recognizer, "recognize_state", observe)
    monkeypatch.setattr(ctx.input_controller, "press_keys", lambda **kw: SimpleNamespace(success=True))

    for _ in range(2):
        result = await server.call_tool("navigate_to", {"target_state": "menu"})
        assert not result.is_error
    assert ctx.storage.recovery_destinations("example", "main") == ([] if mock_ocr else ["menu"])


def test_expired_native_proof_does_not_combine_with_new_attempt(native):
    ctx, _, _ = native
    seed(ctx, "menu", repeats=1)
    old = ctx.storage.db.get_connection()
    with old:
        old.execute("UPDATE navigation_transitions SET updated_at=0 WHERE to_state='menu'")
    seed(ctx, "menu", repeats=1)
    assert ctx.storage.recovery_destinations("example", "main") == []
