"""
Tests for Verified Self-Learning Pipeline (Test D).
Verifies:
- Observe -> Identify -> Plan -> Act -> Verify -> Learn -> Reuse pipeline
- Protection of curated Game Skills (overwriting is rejected)
- Allowlisted action primitives enforcement
- Empirical validation history tracking (candidate -> validated -> verified)
- Session persistence and cross-session reuse
"""

import json
from typing import Any, Dict
import pytest

from ugm.server import create_server, UGMContext
from ugm.core.safety import safety_manager, SafetyViolationError


def extract_data(res: Any) -> Dict[str, Any]:
    if hasattr(res, "structured_content") and res.structured_content:
        if "result" in res.structured_content:
            return res.structured_content["result"]
        return res.structured_content
    if hasattr(res, "content") and res.content:
        try:
            return json.loads(res.content[0].text)
        except Exception:
            pass
    return {}


@pytest.mark.asyncio
async def test_curated_skills_protected_from_overwrite():
    """Verifies that curated Game Skills (e.g. pause_unpause) cannot be overwritten by learned skills."""
    safety_manager.reset_emergency_stop()
    ctx = UGMContext(offline_fixture="tests/fixtures/hoi4_topbar.png")
    server = create_server(ctx)

    # 1. MCP tool call returns structured rejected response
    res = await server.call_tool("learn_skill", {
        "game_id": "hoi4",
        "skill_id": "pause_unpause",
        "name": "Malicious / Rogue Overwrite",
        "steps": [{"action": "press_keys", "params": {"keys": ["space"]}}]
    })
    data = extract_data(res)
    assert data["success"] is False
    assert data["status"] == "rejected"
    assert "cannot overwrite curated game skill" in data["message"].lower()

    # 2. Direct python invocation raises SafetyViolationError
    with pytest.raises(SafetyViolationError):
        ctx.skill_learner.learn_candidate_skill(
            game_id="hoi4",
            skill_id="pause_unpause",
            name="Direct Overwrite",
            steps=[{"action": "press_keys", "params": {"keys": ["space"]}}]
        )


@pytest.mark.asyncio
async def test_disallowed_action_primitives_rejected():
    """Verifies that non-allowlisted action primitives (e.g. arbitrary scripts/commands) are rejected."""
    safety_manager.reset_emergency_stop()
    ctx = UGMContext(offline_fixture="tests/fixtures/hoi4_topbar.png")
    server = create_server(ctx)

    res = await server.call_tool("learn_skill", {
        "game_id": "hoi4",
        "skill_id": "rogue_bash_skill",
        "name": "Disallowed Primitive",
        "steps": [{"action": "execute_bash_command", "params": {"cmd": "rm -rf /"}}]
    })
    data = extract_data(res)
    assert data["success"] is False
    assert data["status"] == "rejected"
    assert "disallowed action primitive" in data["message"].lower()


@pytest.mark.asyncio
async def test_test_d_self_learning_cycle_and_promotion(tmp_path):
    """
    Test D Scenario:
    1. Complete an initially unknown simulated workflow.
    2. Save candidate skill with preconditions and postconditions.
    3. Validate candidate skill through repeated verified executions.
    4. Automatically promote to verified skill.
    5. Close session, reopen new session, and reuse verified skill.
    """
    db_file = str(tmp_path / "self_learning_lifecycle.db")

    # ========================================================
    # SESSION 1: Discovery & Candidate Recording
    # ========================================================
    safety_manager.reset_emergency_stop()
    ctx1 = UGMContext(offline_fixture="tests/fixtures/hoi4_topbar.png", db_path=db_file)
    ctx1.input_controller.dry_run_default = True
    server1 = create_server(ctx1)

    # 1. Discover and save candidate skill
    learn_res = await server1.call_tool("learn_skill", {
        "game_id": "hoi4",
        "skill_id": "learned_research_flow",
        "name": "Learned Research Flow",
        "description": "Opens research panel and waits for stabilization",
        "steps": [
            {"action": "press_keys", "params": {"keys": ["w"], "duration_ms": 60}},
            {"action": "wait", "params": {"duration_ms": 100}}
        ],
        "post_verification": {
            "type": "visual_diff",
            "min_diff_ratio": 0.005
        }
    })
    learn_data = extract_data(learn_res)
    assert learn_data["status"] == "candidate"

    # Inspect learned skills list
    list_res1 = await server1.call_tool("list_learned_skills", {"game_id": "hoi4"})
    list_data1 = extract_data(list_res1)
    assert list_data1["count"] == 1
    assert list_data1["skills"][0]["status"] == "candidate"

    # 2. Validate skill execution #1
    exec1 = await server1.call_tool("execute_skill", {
        "game_id": "hoi4",
        "skill_id": "learned_research_flow",
        "dry_run": True
    })
    assert extract_data(exec1)["success"] is True

    # 3. Validate skill execution #2 (reaches promotion threshold)
    exec2 = await server1.call_tool("execute_skill", {
        "game_id": "hoi4",
        "skill_id": "learned_research_flow",
        "dry_run": True
    })
    assert extract_data(exec2)["success"] is True

    # Check status: should be promoted to 'verified'
    status_res1 = await server1.call_tool("get_learning_status", {"game_id": "hoi4"})
    status_data1 = extract_data(status_res1)
    assert status_data1["verified_skills"] >= 1

    ctx1.storage.db.close()
    del server1
    del ctx1

    # ========================================================
    # SESSION 2: Independent session reuses verified skill
    # ========================================================
    ctx2 = UGMContext(offline_fixture="tests/fixtures/hoi4_topbar.png", db_path=db_file)
    ctx2.input_controller.dry_run_default = True
    server2 = create_server(ctx2)

    list_res2 = await server2.call_tool("list_learned_skills", {"game_id": "hoi4", "status": "verified"})
    list_data2 = extract_data(list_res2)
    assert list_data2["count"] == 1
    verified_skill = list_data2["skills"][0]
    assert verified_skill["skill_id"] == "learned_research_flow"
    assert verified_skill["status"] == "verified"
    assert verified_skill["validation_count"] >= 2

    # Execute reused verified skill
    reuse_exec = await server2.call_tool("execute_skill", {
        "game_id": "hoi4",
        "skill_id": "learned_research_flow",
        "dry_run": True
    })
    assert extract_data(reuse_exec)["success"] is True

    ctx2.storage.db.close()
