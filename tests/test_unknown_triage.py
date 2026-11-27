from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest

from ugm.core.safety import SafetyViolationError
from ugm.core.types import BoundingBox, NormalizedRect, OCRItem
from ugm.server import UGMContext, create_server


def item(text, x=.1, y=.2, confidence=.96):
    return OCRItem(text, confidence, BoundingBox(1, 1, 10, 10), NormalizedRect(x, y, .12, .08))


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    ctx = UGMContext(db_path=tmp_path / "db", ocr_backend="mock", skills_pack_dir=None,
                     offline_fixture="tests/fixtures/sim_fixture.png")
    for method in ("press_keys", "click", "hover"):
        monkeypatch.setattr(ctx.input_controller, method, lambda *a, **kw: pytest.fail("input is forbidden"))
    monkeypatch.setattr(ctx.storage, "record_ui_state", lambda *a, **kw: pytest.fail("write is forbidden"))
    yield ctx, create_server(ctx)
    ctx.storage.db.close()


def snapshots(monkeypatch, ctx, frames, readings):
    count = [0, 0]
    def capture(**kw):
        idx = count[0]
        count[0] += 1
        return frames[idx]
    def read(*a, **kw):
        idx = count[1]
        count[1] += 1
        return SimpleNamespace(items=readings[idx])
    monkeypatch.setattr(ctx.capture_manager, "capture", capture)
    monkeypatch.setattr(ctx.ocr_engine, "read_text", read)
    return count


async def triage(server, **args):
    response = await server.call_tool("triage_ui_state", args)
    return response.structured_content.get("result", response.structured_content)


@pytest.mark.asyncio
async def test_stable_shifted_labels_yield_temporary_observation(fixture, monkeypatch):
    ctx, server = fixture
    frame = np.zeros((40, 60, 3), np.uint8)
    count = snapshots(monkeypatch, ctx, [frame, frame.copy()], [
        [item("Production"), item("Research", .4), item("Population 500", .6)],
        [item(" production ", .12), item("Research", .42), item("Population 502", .6)],
    ])
    out = await triage(server)
    assert out["status"] == "stable_candidate" and out["source"] == "offline_fixture"
    assert out["state_name"] == "unknown" and out["action_suggested"] is False
    assert out["anchors"] == ["production", "research"] and count == [2, 2]
    assert "coordinates" not in repr(out) and "route" not in out


@pytest.mark.asyncio
@pytest.mark.parametrize("unstable", ["visual_change", "ambiguous", "too_few"])
async def test_unstable_or_ambiguous_samples_never_suggest_input(fixture, monkeypatch, unstable):
    ctx, server = fixture
    frame = np.zeros((40, 60, 3), np.uint8)
    first = second = [item("Play"), item("Research", .5)]
    new = frame.copy()
    if unstable == "visual_change":
        new.fill(180)
    elif unstable == "ambiguous":
        second = [*first, item("Play", .6)]
    else:
        second = [item("Play"), item("Credits", .5)]
    count = snapshots(monkeypatch, ctx, [frame, new], [first, second])
    out = await triage(server)
    assert out["status"] == "insufficient_evidence"
    assert out["state_name"] == "unknown" and out["anchors"] == []
    assert out["action_suggested"] is False and count == [2, 2]


@pytest.mark.asyncio
async def test_reattach_after_first_ocr_declines_second_capture(fixture, monkeypatch):
    ctx, server = fixture
    frame = np.zeros((40, 60, 3), np.uint8)
    count = snapshots(monkeypatch, ctx, [frame, frame.copy()], [[item("Play")]] * 2)
    ocr = ctx.ocr_engine.read_text
    def change(*args, **kwargs):
        result = ocr(*args, **kwargs)
        ctx.window_manager.set_mock_window(replace(ctx.window_manager.current_window))
        return result
    monkeypatch.setattr(ctx.ocr_engine, "read_text", change)
    with pytest.raises(SafetyViolationError, match="changed"):
        await triage(server)
    assert count == [1, 1]
