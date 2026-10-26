"""
Integration tests for UGM MCP Server and all 10 MCP tools using official MCP Python SDK v2.
Runs fully offline using mock fixture frame and dry-run execution.
"""

import json
from typing import Any, Dict
import pytest
from ugm.server import create_server, UGMContext
from ugm.core.safety import safety_manager


def extract_data(res: Any) -> Dict[str, Any]:
    """Extracts structured payload from MCP SDK v2 tool call response."""
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
def test_server():
    safety_manager.reset_emergency_stop()
    ctx = UGMContext(offline_fixture="tests/fixtures/hoi4_topbar.png")
    # Set default dry run so no hardware calls are dispatched in CI
    ctx.input_controller.dry_run_default = True
    server = create_server(ctx)
    return server


@pytest.mark.asyncio
async def test_tool_list(test_server):
    tools = await test_server.list_tools()
    tool_names = [t.name for t in tools]

    expected = [
        "get_game_window",
        "capture_screen",
        "read_text",
        "locate_icon",
        "hover_target",
        "click_target",
        "press_keys",
        "verify_selection",
        "emergency_stop",
        "save_training_sample"
    ]
    for exp in expected:
        assert exp in tool_names, f"Tool {exp} must be registered"


@pytest.mark.asyncio
async def test_tool_get_game_window(test_server):
    res = await test_server.call_tool("get_game_window", {"window_title": "Hearts of Iron IV"})
    data = extract_data(res)
    assert data["found"] is True
    assert data["window"]["client_width"] == 1920
    assert data["window"]["client_height"] == 1080


@pytest.mark.asyncio
async def test_tool_capture_screen(test_server):
    res = await test_server.call_tool("capture_screen", {"roi": [0.0, 0.0, 0.5, 0.5]})
    data = extract_data(res)
    assert data["width"] == 960
    assert data["height"] == 540
    assert "capture_id" in data
    assert "image_base64" in data


@pytest.mark.asyncio
async def test_tool_read_text(test_server):
    # OCR topbar political power ROI
    res = await test_server.call_tool("read_text", {"roi": [0.04, 0.0, 0.08, 0.05]})
    data = extract_data(res)
    assert "150" in data["full_text"]
    assert data["count"] >= 1


@pytest.mark.asyncio
async def test_tool_locate_icon(test_server):
    res = await test_server.call_tool("locate_icon", {"icon_name": "civilian_factory", "threshold": 0.85})
    data = extract_data(res)
    assert data["count"] >= 1
    assert data["items"][0]["confidence"] >= 0.85


@pytest.mark.asyncio
async def test_tool_hover_target(test_server):
    res = await test_server.call_tool("hover_target", {"x": 0.5, "y": 0.5, "duration_ms": 50, "dry_run": True})
    data = extract_data(res)
    assert data["target_normalized"] == [0.5, 0.5]


@pytest.mark.asyncio
async def test_tool_click_target(test_server):
    res = await test_server.call_tool("click_target", {"x": 0.2, "y": 0.02, "dry_run": True})
    data = extract_data(res)
    assert data["dry_run"] is True
    assert data["x_normalized"] == 0.2


@pytest.mark.asyncio
async def test_tool_press_keys(test_server):
    res = await test_server.call_tool("press_keys", {"keys": ["space"], "dry_run": True})
    data = extract_data(res)
    assert data["success"] is True
    assert data["dry_run"] is True


@pytest.mark.asyncio
async def test_tool_verify_selection(test_server):
    res = await test_server.call_tool("verify_selection", {"roi": [0.0, 0.0, 0.2, 0.05]})
    data = extract_data(res)
    assert "verified" in data
    assert "diff_ratio" in data


@pytest.mark.asyncio
async def test_tool_emergency_stop_and_block(test_server):
    res = await test_server.call_tool("emergency_stop", {"reason": "Test E-Stop"})
    data = extract_data(res)
    assert data["emergency_stopped"] is True
    assert safety_manager.is_stopped is True

    # Attempt click while stopped
    click_res = await test_server.call_tool("click_target", {"x": 0.5, "y": 0.5, "dry_run": True})
    click_data = extract_data(click_res)
    assert click_data["executed"] is False
    assert "Emergency stop is active" in click_data["reason"]

    safety_manager.reset_emergency_stop()


@pytest.mark.asyncio
async def test_tool_save_training_sample(test_server):
    res = await test_server.call_tool("save_training_sample", {"tag": "topbar_state", "force": True})
    data = extract_data(res)
    assert data["saved"] is True
    assert "sample_id" in data
