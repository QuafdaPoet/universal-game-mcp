"""Test the real MCP control path with injected frames, never OS input."""
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest

from ugm.core.safety import SafetyViolationError
from ugm.core.types import NormalizedRect, OCRItem, WindowInfo
from ugm.server import UGMContext, create_server


@pytest.fixture
def native(tmp_path, monkeypatch):
    monkeypatch.setenv("UGM_SEMANTIC_SELECTOR_LEARNING", "1")
    # Mock is rejected by production; replace the shared reader with an injected test double.
    ctx = UGMContext(ocr_backend="mock", skills_pack_dir=None, db_path=tmp_path / "evidence.db")
    win = WindowInfo(1, "Game", "example.exe", 32, 0, 0, 80, 60, 0, 0, 80, 60, False, True)
    ctx.window_manager.set_mock_window(win)
    frames = []
    def capture(*args, **kw):
        frames.append(True)
        return np.full((60, 80, 3), len(frames), dtype=np.uint8)
    monkeypatch.setattr(ctx.capture_manager, "capture_attached_window", capture)
    monkeypatch.setattr(ctx.state_recognizer, "recognize_state", lambda *a, **kw: SimpleNamespace(
        status="known", state_name="main_menu", confidence=.96))
    ctx.ocr_engine = SimpleNamespace(read_text=lambda *a, **kw: SimpleNamespace(items=[
        OCRItem("Production", .99, None, NormalizedRect(.1, .2, .15, .08)),
    ]))
    for action in ("press_keys", "click", "hover"):
        monkeypatch.setattr(ctx.input_controller, action, lambda *a, **kw: pytest.fail("unexpected input"))
    yield ctx, win, frames
    ctx.storage.db.close()


async def observe(ctx, **kwargs):
    args = {"selector_id":"production", "label":"Production", **kwargs}
    result = await create_server(ctx).call_tool("observe_ui_selector", args)
    return result.structured_content.get("result", result.structured_content)


@pytest.mark.asyncio
async def test_native_producer_repeated_evidence_and_read_only_resolution(native):
    ctx, win, _ = native
    for _ in range(3):
        d = await observe(ctx, record=True)
        assert d["evidence"]["status"] == "candidate"
        assert not d["interactivity_verified"]
    ctx.window_manager.set_mock_window(win)
    d = await observe(ctx, record=True)
    assert d["evidence"]["status"] == "repeat_observed"
    resolved = await observe(ctx)
    assert resolved["evidence"]["observations"] == 4
    assert resolved["match"] == "found" and resolved["bbox_normalized"]["x"] == .1


@pytest.mark.asyncio
async def test_opt_in_and_dry_run_cannot_write(native):
    ctx, _, frames = native
    ctx._semantic_selector_learning = False
    with pytest.raises(SafetyViolationError, match="disabled"):
        await observe(ctx, record=True)
    assert not frames
    ctx._semantic_selector_learning = True
    ctx.input_controller.dry_run_default = True
    with pytest.raises(SafetyViolationError, match="dry.run"):
        await observe(ctx, record=True)
    assert not frames


@pytest.mark.asyncio
async def test_unknown_and_invalidated_scopes_cannot_persist(native, monkeypatch):
    ctx, win, _ = native
    with monkeypatch.context() as m:
        m.setattr(ctx.state_recognizer, "recognize_state", lambda *a, **kw: SimpleNamespace(
            status="unknown", state_name="unknown", confidence=.5))
        assert (await observe(ctx, record=True))["match"] == "unknown_state"
    with monkeypatch.context() as m:
        def changed(*a, **kw):
            ctx.window_manager.set_mock_window(replace(win))
            return SimpleNamespace(items=[])
        m.setattr(ctx.ocr_engine, "read_text", changed)
        with pytest.raises(SafetyViolationError, match="changed"):
            await observe(ctx, record=True)
    assert (await observe(ctx))["evidence"]["status"] == "unseen"


@pytest.mark.asyncio
async def test_mock_ocr_cannot_issue_receipt(native):
    from ugm.core.ocr import MockOCREngine

    ctx, _, frames = native
    ctx.ocr_engine = MockOCREngine()
    with pytest.raises(SafetyViolationError, match="real OCR"):
        await observe(ctx, record=True)
    assert frames == []


def test_failed_precommit_scope_check_rolls_back_entire_receipt(native):
    from ugm.memory.selector_evidence import Receipt, SelectorEvidenceStore, SelectorRecipe

    ctx, _, _ = native
    ledger = SelectorEvidenceStore(ctx.storage.db)
    recipe = SelectorRecipe("example", "main_menu", "production", "Production")
    items = ctx.ocr_engine.read_text(None).items
    def reject():
        raise SafetyViolationError("scope changed")
    with pytest.raises(SafetyViolationError):
        ledger.record(recipe, Receipt("a"*32, "b"*64, "c"*32, "example", "main_menu", "live_observation"),
                      items, validate_before_commit=reject)
    assert ledger.status(recipe).status == "unseen"
