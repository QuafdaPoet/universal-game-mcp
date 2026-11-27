"""The optimize MCP tool stages an isolated revision and never accepts claimed trial metrics."""
from pathlib import Path

import pytest

from ugm.server import UGMContext, create_server

FIXTURE = Path(__file__).parent / "fixtures" / "hoi4_topbar.png"
ACTIVE = [{"action": "press_keys", "params": {"keys": ["space"]}}]
CANDIDATE = [{"action": "press_keys", "params": {"keys": ["t"]}}]


async def call(server, **args):
    r = await server.call_tool("optimize_skill", args)
    assert not r.is_error
    return r.structured_content.get("result", r.structured_content)


@pytest.mark.asyncio
async def test_proposal_does_not_change_active_or_allow_unverified_promotion(tmp_path):
    ctx = UGMContext(offline_fixture=str(FIXTURE), db_path=tmp_path / "memory.db", ocr_backend="mock")
    server = create_server(ctx)
    ctx.storage.save_learned_skill(
        game_id="demo", skill_id="toggle", name="Toggle", steps=ACTIVE,
        post_verification={"expected_text": "Paused"}, status="verified",
    )
    old = ctx.storage.get_learned_skill("toggle", game_id="demo")
    staged = await call(server, skill_id="toggle", game_id="demo", steps=CANDIDATE)
    assert staged["success"] and not staged["promoted"]
    candidate = staged["candidate"]
    assert candidate["steps"] == CANDIDATE
    assert ctx.storage.get_learned_skill("toggle", game_id="demo") == old

    executed = ctx.skill_engine.execute_skill("toggle", game_id="demo", dry_run=True)
    assert executed.dry_run and not executed.post_verification_passed
    assert executed.step_logs[0]["evidence"]["keys"] == ["space"]

    inspected = await call(server, skill_id="toggle", game_id="demo", operation="inspect",
                           candidate_id=candidate["candidate_id"])
    assert inspected["success"] and inspected["candidate"] == candidate

    denied = await call(server, skill_id="toggle", game_id="demo", operation="promote",
                        candidate_id=candidate["candidate_id"])
    assert not denied["success"] and not denied["promoted"]
    assert ctx.storage.get_learned_skill("toggle", game_id="demo") == old


@pytest.mark.asyncio
async def test_learning_retry_cannot_replace_or_demote_an_active_verified_body(tmp_path):
    ctx = UGMContext(offline_fixture=str(FIXTURE), db_path=tmp_path / "memory.db", ocr_backend="mock")
    server = create_server(ctx)
    ctx.storage.save_learned_skill(
        game_id="demo", skill_id="toggle", name="Toggle", steps=ACTIVE,
        preconditions={"window_focused": True}, status="verified", source="synthetic_fixture",
    )
    old = ctx.storage.get_learned_skill("toggle", game_id="demo")

    async def learn(steps):
        raw = await server.call_tool("learn_skill", {
            "game_id": "demo", "skill_id": "toggle", "name": "Toggle",
            "steps": steps, "initial_confidence": 0.1,
        })
        return raw.structured_content.get("result", raw.structured_content)

    assert not (await learn(CANDIDATE))["success"]
    assert (await learn(ACTIVE))["status"] == "verified"
    assert ctx.storage.get_learned_skill("toggle", game_id="demo") == old


@pytest.mark.asyncio
async def test_client_cannot_supply_live_benchmark_claims(tmp_path, monkeypatch):
    from ugm.core.capture import CaptureManager, FixtureBackend

    # Test the live knowledge namespace without accessing any physical capture or input.
    monkeypatch.setattr(CaptureManager, "_init_preferred_backend", lambda _: FixtureBackend())
    ctx = UGMContext(db_path=tmp_path / "live.db", ocr_backend="mock")
    server = create_server(ctx)
    ctx.storage.save_learned_skill(
        game_id="demo", skill_id="toggle", name="Toggle", steps=ACTIVE,
        post_verification={"expected_text": "Paused"}, status="verified",
    )
    staged = await call(server, skill_id="toggle", game_id="demo", steps=CANDIDATE)
    candidate_id = staged["candidate"]["candidate_id"]
    denied = await server.call_tool("optimize_skill", {
        "skill_id": "toggle", "game_id": "demo", "operation": "promote",
        "candidate_id": candidate_id, "trial_count": 100, "median_speedup": 0.99,
        "postcondition_passed": True, "source": "live", "dry_run": False,
    })
    if not denied.is_error:
        data = denied.structured_content.get("result", denied.structured_content)
        assert not data["promoted"]
        assert data["reason"] == "insufficient_live_benchmark_pairs"
    assert ctx.storage.get_skill_optimization_candidate("demo", "toggle", candidate_id).trial_count == 0
    assert ctx.storage.get_learned_skill("toggle", game_id="demo").steps == ACTIVE
