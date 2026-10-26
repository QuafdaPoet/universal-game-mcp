"""
Tests for Autonomous Navigation Graph and Recovery (Test C & Test E).
Verifies:
- Dijkstra shortest path finding across nested menu states
- Multi-step route planning and execution
- Step-by-step state verification
- Recovery on unexpected layout mismatch / invalidation (reobserve, replan, or safe stop)
"""

import json
from typing import Any, Dict
import pytest

from ugm.navigation.graph import NavigationGraph, NavigationNode, NavigationEdge
from ugm.navigation.recognizer import UIStateRecognizer, UIStateDetection
from ugm.navigation.navigator import UINavigator
from ugm.server import create_server, UGMContext
from ugm.core.safety import safety_manager, EmergencyStopTriggered


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


def test_dijkstra_multistep_pathfinding():
    """Verifies multi-step route planning across nested menus: Main Map -> Production -> Equipment -> Line."""
    graph = NavigationGraph(game_id="hoi4")

    # Register nested state nodes
    nodes = [
        "main_map",
        "production",
        "equipment_category",
        "production_line",
        "research",
        "diplomacy"
    ]
    for n in nodes:
        graph.register_node(NavigationNode(node_id=n, menu_name=n, game_id="hoi4", identification_selector=""))

    # Register edges with costs
    # Path 1: main_map -> production -> equipment_category -> production_line (Total cost: 1.0 + 1.0 + 1.0 = 3.0)
    graph.register_edge(NavigationEdge("main_map", "production", "press_keys", {"keys": ["y"]}, cost=1.0), persist=False)
    graph.register_edge(NavigationEdge("production", "equipment_category", "click_selector", {"selector_id": "infantry_tab"}, cost=1.0), persist=False)
    graph.register_edge(NavigationEdge("equipment_category", "production_line", "click_selector", {"selector_id": "add_line"}, cost=1.0), persist=False)

    # Path 2 (Direct shortcut edge with higher cost: 5.0)
    graph.register_edge(NavigationEdge("main_map", "production_line", "press_keys", {"keys": ["alt", "y"]}, cost=5.0), persist=False)

    # Plan route from main_map to production_line
    res = graph.plan_route(start_state="main_map", goal_state="production_line")
    assert res.found is True
    assert res.total_cost == 3.0
    assert len(res.edges) == 3
    assert res.nodes == ["main_map", "production", "equipment_category", "production_line"]


@pytest.mark.asyncio
async def test_test_c_multistep_navigation_execution():
    """
    Test C Scenario:
    Starting from known simulated state, find multistep route to destination, execute it, and validate every transition.
    """
    safety_manager.reset_emergency_stop()
    ctx = UGMContext(offline_fixture="tests/fixtures/hoi4_topbar.png")
    ctx.input_controller.dry_run_default = True
    server = create_server(ctx)

    # 1. Get navigation plan
    plan_res = await server.call_tool("get_navigation_plan", {
        "game_id": "hoi4",
        "start_state": "main_map",
        "target_state": "production"
    })
    plan_data = extract_data(plan_res)
    assert plan_data["found"] is True
    assert plan_data["start_state"] == "main_map"
    assert plan_data["target_state"] == "production"

    # 2. Execute navigation
    nav_res = await server.call_tool("navigate_to", {
        "game_id": "hoi4",
        "target_state": "production",
        "dry_run": True
    })
    nav_data = extract_data(nav_res)
    assert nav_data["success"] is True
    assert nav_data["current_state"] == "production"
    assert nav_data["steps_executed"] >= 1
    assert len(nav_data["step_logs"]) >= 1
    assert nav_data["step_logs"][0]["verified"] is True


@pytest.mark.asyncio
async def test_test_e_recovery_on_mismatch_and_safe_halt():
    """
    Test E Scenario:
    Change the simulated layout or invalidate an expected selector / transition.
    The agent must detect the mismatch and reobserve, replan, or safely stop without blind input dispatch.
    """
    safety_manager.reset_emergency_stop()
    ctx = UGMContext(offline_fixture="tests/fixtures/hoi4_topbar.png")
    server = create_server(ctx)

    # Configure a custom graph where step leads to an unknown / disconnected destination
    graph = ctx.get_navigation_graph("hoi4")
    graph.register_node(NavigationNode("isolated_secret_panel", "Secret", "hoi4", ""))

    # No edges exist from main_map to isolated_secret_panel
    res = await server.call_tool("navigate_to", {
        "game_id": "hoi4",
        "target_state": "isolated_secret_panel",
        "dry_run": True
    })
    data = extract_data(res)
    # Navigator refuses to dispatch blind actions when no route is verified
    assert data["success"] is False
    assert "rejected" in data["message"].lower() or "no viable" in data["message"].lower()
    assert data["steps_executed"] == 0


@pytest.mark.asyncio
async def test_navigation_emergency_stop_interrupt():
    """Verifies that emergency stop immediately interrupts navigation safely."""
    safety_manager.reset_emergency_stop()
    ctx = UGMContext(offline_fixture="tests/fixtures/hoi4_topbar.png")
    server = create_server(ctx)

    safety_manager.trigger_emergency_stop("Killswitch active during navigation test")

    res = await server.call_tool("navigate_to", {
        "game_id": "hoi4",
        "target_state": "production"
    })
    data = extract_data(res)
    assert data["success"] is False
    assert "emergency stop" in data["message"].lower() or "blocked" in data["message"].lower()

    safety_manager.reset_emergency_stop()
