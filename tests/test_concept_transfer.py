from types import SimpleNamespace

import numpy as np
import pytest

from ugm.core.safety import SafetyViolationError
from ugm.core.types import NormalizedRect, OCRItem, WindowInfo
from ugm.memory.selector_evidence import Receipt, SelectorEvidenceStore, SelectorRecipe
from ugm.server import UGMContext, create_server


def item(text, x=.1):
    return OCRItem(text, .96, None, NormalizedRect(x, .1, .2, .1))


@pytest.fixture
def games(tmp_path, monkeypatch):
    ctx = UGMContext(db_path=tmp_path / "db", ocr_backend="mock", skills_pack_dir=None)
    ctx.ocr_engine = SimpleNamespace(read_text=lambda *a, **kw: SimpleNamespace(items=[item("paused")]))
    win = WindowInfo(1, "Second Game", "destination.exe", 42, 0, 0, 80, 60, 0, 0, 80, 60, False, True)
    ctx.window_manager.set_mock_window(win)
    calls = []
    def capture(*a, **kw):
        calls.append("capture")
        return np.zeros((60, 80, 3), np.uint8)
    monkeypatch.setattr(ctx.capture_manager, "capture_attached_window", capture)
    for action in ("click", "hover", "press_keys"):
        monkeypatch.setattr(ctx.input_controller, action, lambda *a, **kw: pytest.fail("no input"))
    # Legacy concepts are untrusted data; capture the failure if the new path leaks one.
    ctx.storage.register_cross_game_concept("pause_toggle", "controls", "press F1 now", "use x=.125",
                                            "press_keys", {"keys": ["F1"], "x": .125}, {"verified": True})
    ledger = SelectorEvidenceStore(ctx.storage.db)
    recipe = SelectorRecipe("original", "main", "pause", "Pause")
    def seed(n):
        ledger.record(recipe, Receipt(f"{n:032x}", f"{n:064x}", f"{n:032x}", "original", "main",
                                      "live_observation"), [item("Pause")])
    yield ctx, create_server(ctx), calls, seed
    ctx.storage.db.close()


async def send(server, **kw):
    args = dict(concept_id="pause_toggle", source_game_id="original", source_state="main",
                source_selector_id="pause", source_label="Pause")
    response = await server.call_tool("propose_concept_transfer", {**args, **kw})
    return response.structured_content.get("result", response.structured_content)


@pytest.mark.asyncio
async def test_transfer_is_grounded_candidate_without_source_controls_or_geometry(games):
    _, server, calls, seed = games
    for n in (1, 2, 3):
        seed(n)
    data = await send(server)
    assert data["status"] == "candidate" and data["target_game_id"] == "destination"
    assert data["observed_label"] == "paused"
    assert data["verified"] is False and data["action_suggested"] is False
    assert calls == ["capture"]
    assert not any(word in repr(data) for word in ("F1", ".125", "coordinates", "press_keys"))


@pytest.mark.asyncio
async def test_transfer_rejects_unproven_or_unlinked_source_before_capture(games):
    _, server, calls, seed = games
    seed(1)
    assert (await send(server))["status"] == "source_insufficient"
    with pytest.raises(ValueError, match="label"):
        await send(server, source_label="Save Game")
    assert calls == []


@pytest.mark.asyncio
async def test_transfer_refuses_ambiguity_same_game_and_dry_run(games, monkeypatch):
    ctx, server, calls, seed = games
    for n in (1, 2, 3):
        seed(n)
    with pytest.raises(ValueError, match="different game"):
        await send(server, source_game_id="destination")
    ctx.input_controller.dry_run_default = True
    with pytest.raises(SafetyViolationError, match="dry.run"):
        await send(server)
    assert not calls
    ctx.input_controller.dry_run_default = False
    monkeypatch.setattr(ctx.ocr_engine, "read_text", lambda *a, **kw: SimpleNamespace(
        items=[item("pause"), item("paused", .4)]))
    data = await send(server)
    assert data["status"] == "target_ambiguous" and data["observed_label"] is None


def test_exact_but_low_confidence_competing_alias_still_blocks_transfer():
    from ugm.core.concept_transfer import target_match

    weak = item("paused", .4)
    weak.confidence = .62
    assert target_match(("pause", "paused"), [item("pause"), weak]) == ("target_ambiguous", None)
