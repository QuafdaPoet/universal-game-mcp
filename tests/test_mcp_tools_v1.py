"""
Integration tests for V1 prioritized MCP tools: locate_ui_element, verify_state,
and dynamic click_target without fixed coordinates.
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
def v1_test_server():
    safety_manager.reset_emergency_stop()
    ctx = UGMContext(offline_fixture="tests/fixtures/hoi4_topbar.png")
    ctx.input_controller.dry_run_default = True
    server = create_server(ctx)
    return server


@pytest.mark.asyncio
async def test_mcp_tool_locate_ui_element_by_icon(v1_test_server):
    res = await v1_test_server.call_tool(
        "locate_ui_element",
        {"icon_name": "civilian_factory", "threshold": 0.85}
    )
    data = extract_data(res)
    assert data["status"] == "found"
    assert data["count"] >= 1
    assert data["is_ambiguous"] is False
    assert data["items"][0]["confidence"] >= 0.85


@pytest.mark.asyncio
async def test_mcp_tool_locate_ui_element_by_registered_selector(v1_test_server):
    # Selector 'topbar_factories' is declared in game_skills/hoi4
    res = await v1_test_server.call_tool(
        "locate_ui_element",
        {"selector_id": "topbar_factories"}
    )
    data = extract_data(res)
    assert data["status"] in ("found", "ambiguous")
    assert "count" in data


@pytest.mark.asyncio
async def test_mcp_tool_click_target_with_dynamic_selector(v1_test_server):
    """Clicks a target resolved dynamically via selector rather than fixed coordinates."""
    res = await v1_test_server.call_tool(
        "click_target",
        {
            "icon_name": "civilian_factory",
            "dry_run": True,
            "verify": True
        }
    )
    data = extract_data(res)
    assert data["status"] == "success"
    assert data["dry_run"] is True
    # Coordinates were dynamically computed, not 0
    assert data["x_normalized"] > 0.0
    assert data["y_normalized"] >= 0.0


@pytest.mark.asyncio
async def test_mcp_tool_verify_state(v1_test_server):
    res = await v1_test_server.call_tool(
        "verify_state",
        {"roi": [0.0, 0.0, 0.3, 0.06]}
    )
    data = extract_data(res)
    assert "verified" in data
    assert "diff_ratio" in data
    assert "message" in data
