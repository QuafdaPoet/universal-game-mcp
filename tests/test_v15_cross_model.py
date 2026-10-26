"""
Integration tests for Cross-Model Knowledge Reuse (Test B).
Verifies that knowledge discovered and verified by one AI agent client (e.g. Gemini)
is stored in Universal Game MCP's persistent SQLite database and can be immediately
retrieved, navigated, or executed by a completely different independent AI agent client
(e.g. DeepSeek or Claude) without fine-tuning, retraining, or rediscovering it.
"""

import json
from typing import Any, Dict
import pytest

from ugm.server import create_server, UGMContext
from ugm.core.safety import safety_manager


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
async def test_cross_model_knowledge_reuse(tmp_path):
    """
    Test B Scenario:
    Client 1 (Gemini session) connects to UGM, discovers and verifies a candidate skill and navigation transition.
    Client 1 disconnects.
    Client 2 (DeepSeek session) connects to UGM using the same persistent knowledge store.
    Client 2 searches memory, finds the verified transition/skill, and navigates/executes it.
    """
    db_file = str(tmp_path / "cross_model_reuse.db")

    # ========================================================
    # SESSION 1: Client 1 (representing Gemini) connects
    # ========================================================
    safety_manager.reset_emergency_stop()
    ctx1 = UGMContext(offline_fixture="tests/fixtures/hoi4_topbar.png", db_path=db_file)
    ctx1.input_controller.dry_run_default = True
    server1 = create_server(ctx1)

    # 1a. Client 1 learns a new candidate skill
    learn_res = await server1.call_tool("learn_skill", {
        "game_id": "hoi4",
        "skill_id": "gemini_quick_diplomacy",
        "name": "Gemini Quick Diplomacy Route",
        "description": "Opens diplomacy tab via hotkey 'e'",
        "steps": [
            {"action": "press_keys", "params": {"keys": ["e"], "duration_ms": 50}}
        ],
        "post_verification": {
            "type": "visual_diff",
            "min_diff_ratio": 0.005
        }
    })
    learn_data = extract_data(learn_res)
    assert learn_data["status"] == "candidate"

    # 1b. Client 1 verifies the skill
    exec_res1 = await server1.call_tool("execute_skill", {
        "game_id": "hoi4",
        "skill_id": "gemini_quick_diplomacy",
        "dry_run": True
    })
    exec_data1 = extract_data(exec_res1)
    assert exec_data1["success"] is True

    # 1c. Client 1 promotes or records transition
    ctx1.storage.record_transition(
        game_id="hoi4",
        from_state="main_map",
        to_state="diplomacy",
        action_type="press_keys",
        params={"keys": ["e"]},
        success=True,
        cost=1.0,
        source="live"
    )

    # Disconnect Client 1 / Close Session 1
    ctx1.storage.db.close()
    del server1
    del ctx1

    # ========================================================
    # SESSION 2: Client 2 (representing DeepSeek) connects
    # ========================================================
    ctx2 = UGMContext(offline_fixture="tests/fixtures/hoi4_topbar.png", db_path=db_file)
    ctx2.input_controller.dry_run_default = True
    server2 = create_server(ctx2)

    # 2a. Client 2 queries memory search for learned skills
    mem_res = await server2.call_tool("memory_search", {
        "game_id": "hoi4",
        "search_type": "skills",
        "query": "gemini"
    })
    mem_data = extract_data(mem_res)
    assert "learned_skills" in mem_data
    found_skills = mem_data["learned_skills"]
    assert len(found_skills) == 1
    assert found_skills[0]["skill_id"] == "gemini_quick_diplomacy"

    # 2b. Client 2 can plan navigation using the transition learned by Client 1
    nav_plan_res = await server2.call_tool("get_navigation_plan", {
        "game_id": "hoi4",
        "start_state": "main_map",
        "target_state": "diplomacy"
    })
    plan_data = extract_data(nav_plan_res)
    assert plan_data["found"] is True
    assert plan_data["steps_count"] >= 1
    assert plan_data["steps"][0]["target_node_id"] == "diplomacy"

    # 2c. Client 2 executes the learned skill without re-discovery
    exec_res2 = await server2.call_tool("execute_skill", {
        "game_id": "hoi4",
        "skill_id": "gemini_quick_diplomacy",
        "dry_run": True
    })
    exec_data2 = extract_data(exec_res2)
    assert exec_data2["success"] is True
    assert exec_data2["skill_id"] == "gemini_quick_diplomacy"

    ctx2.storage.db.close()
