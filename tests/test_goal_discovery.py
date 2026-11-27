"""Goal scans return bounded evidence, not executable guesses."""
import numpy as np
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from ugm.core.explorer import UIElementCandidate
from ugm.core.types import NormalizedRect
from ugm.server import UGMContext, create_server


@pytest.fixture
def ctx(tmp_path):
    context = UGMContext(db_path=tmp_path / "goal.db", ocr_backend="mock", skills_pack_dir=None,
                         offline_fixture="tests/fixtures/hoi4_topbar.png")
    yield context
    context.storage.db.close()


def fail(*args, **kwargs):
    pytest.fail("Unexpected capture or action during goal test")


def label(name, score, x=0.1, source="ocr"):
    rect = NormalizedRect(x, .1, .2, .12)
    return UIElementCandidate(name, "button", rect, rect.center, name,
                              details={"source": source, "raw_score": score})


@pytest.mark.asyncio
async def test_goal_scan_grounded_bounded_and_read_only(ctx, monkeypatch):
    calls = []
    def scan(frame, roi, scan_mode):
        assert isinstance(frame, np.ndarray)
        calls.append(scan_mode)
        return [
            label("Production", .92),
            label("Production Queue", .70),
            label("Production Queue Europe", .90),
            label("Production Queue Shape", .99, source="contour"),
            label("Production Queue Outside", 1, x=0.95),
            label("Production Queue Score", float("nan")),
            label("Reproduction", .99),
        ]
    monkeypatch.setattr(ctx.explorer, "discover_interactive_elements", scan)
    monkeypatch.setattr(ctx.storage, "record_ui_state", fail)
    monkeypatch.setattr(ctx.input_controller, "click", fail)
    monkeypatch.setattr(ctx.input_controller, "hover", fail)
    monkeypatch.setattr(ctx.input_controller, "press_keys", fail)
    result = await create_server(ctx).call_tool("explore_interactive_elements", {
        "goal": "ＰＲＯＤＵＣＴＩＯＮ queue", "max_results": 2,
    })
    assert not result.is_error and calls == ["text"]
    data = result.structured_content.get("result", result.structured_content)
    assert data["total_matches"] == 3 and data["count"] == 2 and data["truncated"]
    assert [row["label"] for row in data["candidates"]] == ["Production Queue Europe", "Production Queue"]
    assert all(row["matched_terms"] == ["production", "queue"] for row in data["candidates"])
    assert all(row["observation_source"] == "offline_fixture" for row in data["candidates"])
    assert all(not row["interactivity_verified"] and "suggested_action" not in row for row in data["candidates"])


@pytest.mark.asyncio
async def test_goal_scan_no_match_proposes_no_action(ctx, monkeypatch):
    monkeypatch.setattr(ctx.explorer, "discover_interactive_elements",
                        lambda *args, **kwargs: [label("Settings", .99)])
    result = await create_server(ctx).call_tool("explore_interactive_elements", {"goal": "Continue"})
    data = result.structured_content.get("result", result.structured_content)
    assert data["candidates"] == [] and data["count"] == 0 and not data["truncated"]


@pytest.mark.asyncio
@pytest.mark.parametrize("args", [
    {"goal": "???"}, {"goal": "x" * 161}, {"goal": "Play", "scan_mode": "shapes"},
    {"goal": "Play", "max_results": 13},
])
async def test_invalid_goal_does_not_capture(ctx, monkeypatch, args):
    monkeypatch.setattr(ctx, "capture_verification_frame", fail)
    with pytest.raises((ValueError, ToolError)):
        await create_server(ctx).call_tool("explore_interactive_elements", args)


@pytest.mark.asyncio
async def test_goal_scan_discards_candidates_when_attachment_changes(ctx, monkeypatch):
    from ugm.core.safety import SafetyViolationError
    def scan(*args, **kwargs):
        ctx.window_manager.set_mock_window(ctx.window_manager.current_window)
        return [label("Play", .99)]
    monkeypatch.setattr(ctx.explorer, "discover_interactive_elements", scan)
    with pytest.raises(SafetyViolationError, match="changed"):
        await create_server(ctx).call_tool("explore_interactive_elements", {"goal": "Play"})


@pytest.mark.asyncio
async def test_goal_scan_with_onnx_ocr_on_synthetic_fixture(tmp_path):
    """Real OCR on an artificial screenshot; no seeded skill pack or native input."""
    pytest.importorskip("rapidocr_onnxruntime")
    context = UGMContext(
        offline_fixture="tests/fixtures/sim_fixture.png",
        db_path=tmp_path / "ocr-goal.db", skills_pack_dir=None, ocr_intra_op_threads=2,
    )
    try:
        server = create_server(context)
        async def run(goal):
            result = await server.call_tool("explore_interactive_elements", {
                "goal": goal, "max_results": 2, "roi": [.22, 0, .17, .15],
            })
            assert not result.is_error
            return result.structured_content.get("result", result.structured_content)

        positive = await run("gold")
        assert positive["count"] == 1 and positive["observation_source"] == "offline_fixture"
        candidate, = positive["candidates"]
        assert "".join(candidate["label"].casefold().split()) == "gold:500"
        assert candidate["matched_terms"] == ["gold"] and not candidate["interactivity_verified"]
        assert .22 <= candidate["bbox_normalized"]["x"] < .39
        assert (await run("spaceship"))["candidates"] == []
    finally:
        context.storage.db.close()
