"""
Integration tests for V1.5 MCP Tools:
- list_game_packs
- get_ui_state
- memory_search
- get_navigation_plan
- navigate_to
- execute_skill
- learn_skill
- list_learned_skills
- get_learning_status
- delete_learned_memory
- index_game_assets
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


@pytest.fixture
def v15_test_server(tmp_path):
    safety_manager.reset_emergency_stop()
    ctx = UGMContext(
        db_path=tmp_path / "mcp_v15_test.db",
        offline_fixture="tests/fixtures/hoi4_topbar.png"
    )
    ctx.input_controller.dry_run_default = True
    server = create_server(ctx)
    return server, ctx


@pytest.mark.asyncio
async def test_tool_list_game_packs(v15_test_server):
    server, ctx = v15_test_server
    res = await server.call_tool("list_game_packs", {})
    data = extract_data(res)
    assert "count" in data
    assert data["count"] >= 1
    pack_ids = [p["game_id"] for p in data["packs"]]
    assert "hoi4" in pack_ids


@pytest.mark.asyncio
async def test_tool_get_ui_state(v15_test_server):
    server, ctx = v15_test_server
    res = await server.call_tool("get_ui_state", {"game_id": "hoi4"})
    data = extract_data(res)
    assert "state_name" in data
    assert "confidence" in data
    assert "status" in data
    assert "signals" in data
    assert "evidence" in data


@pytest.mark.asyncio
async def test_tool_memory_search(v15_test_server):
    server, ctx = v15_test_server
    # Record a state to search for
    ctx.storage.record_ui_state(
        game_id="hoi4",
        state_name="main_map",
        phash="0000111100001111",
        confidence=0.95
    )

    res = await server.call_tool("memory_search", {"game_id": "hoi4", "query": "main", "search_type": "states"})
    data = extract_data(res)
    assert data["game_id"] == "hoi4"
    assert len(data.get("states", [])) >= 1
    assert data["states"][0]["state_name"] == "main_map"


@pytest.mark.asyncio
async def test_tool_get_navigation_plan(v15_test_server):
    server, ctx = v15_test_server
    res = await server.call_tool(
        "get_navigation_plan",
        {"start_state": "main_map", "target_state": "production", "game_id": "hoi4"}
    )
    data = extract_data(res)
    assert data["found"] is True
    assert data["target_state"] == "production"
    assert data["steps_count"] >= 1


@pytest.mark.asyncio
async def test_tool_navigate_to(v15_test_server):
    server, ctx = v15_test_server
    res = await server.call_tool(
        "navigate_to",
        {"target_state": "production", "game_id": "hoi4", "dry_run": True}
    )
    data = extract_data(res)
    assert data["target_state"] == "production"
    assert data["dry_run"] is True


@pytest.mark.asyncio
async def test_tool_execute_skill(v15_test_server):
    server, ctx = v15_test_server
    res = await server.call_tool(
        "execute_skill",
        {"skill_id": "pause_unpause", "game_id": "hoi4", "dry_run": True}
    )
    data = extract_data(res)
    assert data["skill_id"] == "pause_unpause"
    assert data["dry_run"] is True


@pytest.mark.asyncio
async def test_tool_learn_and_list_learned_skills(v15_test_server):
    server, ctx = v15_test_server
    # 1. Learn skill
    learn_res = await server.call_tool(
        "learn_skill",
        {
            "skill_id": "test_mcp_learned_skill",
            "name": "Test MCP Learned Skill",
            "game_id": "hoi4",
            "steps": [
                {"action": "press_keys", "params": {"keys": ["space"], "duration_ms": 50}}
            ],
            "description": "Learned via MCP test"
        }
    )
    learn_data = extract_data(learn_res)
    assert learn_data["success"] is True
    assert learn_data["status"] == "candidate"

    # 2. List learned skills
    list_res = await server.call_tool("list_learned_skills", {"game_id": "hoi4"})
    list_data = extract_data(list_res)
    assert list_data["count"] >= 1
    skill_ids = [s["skill_id"] for s in list_data["skills"]]
    assert "test_mcp_learned_skill" in skill_ids


@pytest.mark.asyncio
async def test_tool_get_learning_status(v15_test_server):
    server, ctx = v15_test_server
    res = await server.call_tool("get_learning_status", {"game_id": "hoi4"})
    data = extract_data(res)
    assert "ui_states_count" in data
    assert "transitions_count" in data
    assert "total_learned_skills" in data
    assert "action_history_count" in data


@pytest.mark.asyncio
async def test_tool_delete_learned_memory(v15_test_server):
    server, ctx = v15_test_server
    # Learn a skill first
    await server.call_tool(
        "learn_skill",
        {
            "skill_id": "skill_to_delete",
            "name": "To Delete",
            "game_id": "hoi4",
            "steps": [{"action": "press_keys", "params": {"keys": ["x"]}}]
        }
    )

    # Delete specific skill
    del_res = await server.call_tool(
        "delete_learned_memory",
        {"skill_id": "skill_to_delete"}
    )
    del_data = extract_data(del_res)
    assert del_data["deleted"] is True
    assert del_data["skill_id"] == "skill_to_delete"


@pytest.mark.asyncio
async def test_tool_index_game_assets(v15_test_server, tmp_path):
    server, ctx = v15_test_server
    # Test indexing a non-existent or empty folder gracefully
    res = await server.call_tool(
        "index_game_assets",
        {"game_path": str(tmp_path / "hoi4_fake"), "game_id": "hoi4"}
    )
    data = extract_data(res)
    assert "game_id" in data
    assert data["success"] is False
    assert "does not exist" in data["message"]
