"""MCP sequences must not act or learn after the native observation scope changes."""
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest

from ugm.core.types import WindowInfo
from ugm.navigation.graph import NavigationEdge
from ugm.server import UGMContext, create_server


@pytest.fixture
def native(tmp_path, monkeypatch):
    ctx = UGMContext(db_path=tmp_path / "scope.db", ocr_backend="mock", skills_pack_dir=None)
    win = WindowInfo(1, "Game", "testgame.exe", 42, 0, 0, 80, 60, 0, 0, 80, 60, False, True)
    ctx.window_manager.set_mock_window(win)
    calls = []
    frame = np.zeros((60, 80, 3), dtype=np.uint8)

    def scoped_capture(manager, expected):
        assert manager is ctx.window_manager and expected == win
        calls.append("native")
        return frame

    def generic_capture(**kwargs):
        calls.append("unscoped")
        return frame

    monkeypatch.setattr(ctx.capture_manager, "capture", generic_capture)
    monkeypatch.setattr(ctx.capture_manager, "capture_attached_window", scoped_capture)
    yield ctx, win, calls
    ctx.storage.db.close()


def payload(result):
    return result.structured_content.get("result", result.structured_content)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["reattach", "blur", "backend"])
async def test_navigation_stops_after_dispatch_when_scope_changes(native, monkeypatch, change):
    ctx, win, captures = native
    graph = ctx.get_navigation_graph("testgame")
    for source, destination in [("start", "middle"), ("middle", "goal")]:
        graph.register_edge(NavigationEdge(source, destination, "press_keys", {"keys": ["space"]}), persist=False)
    seen, keys, feedback = [], [], []
    def recognize(*args, **kwargs):
        seen.append(kwargs)
        return SimpleNamespace(state_name=["start", "middle", "goal"][len(seen)-1], status="known")

    def press(**kwargs):
        keys.append(kwargs.get("target_window"))
        if len(keys) == 1:
            if change == "reattach":
                ctx.window_manager.set_mock_window(win)
            elif change == "blur":
                ctx.window_manager.set_mock_window(replace(win, is_focused=False))
            else:
                ctx.capture_manager.set_backend(SimpleNamespace(release=lambda: None))
        return SimpleNamespace(success=True)

    monkeypatch.setattr(ctx.state_recognizer, "recognize_state", recognize)
    monkeypatch.setattr(ctx.input_controller, "press_keys", press)
    monkeypatch.setattr(graph, "record_transition_result", lambda **kw: feedback.append(kw))
    data = payload(await create_server(ctx).call_tool("navigate_to", {"target_state": "goal"}))
    assert not data["success"] and data["steps_executed"] == 1
    assert not data["step_logs"][0]["verified"]
    assert keys == [win] and captures == ["native"] and not feedback and len(seen) == 1


@pytest.mark.asyncio
async def test_skill_does_not_credit_uncertain_capture_or_issue_next_key(native, monkeypatch):
    ctx, win, captures = native
    engine = ctx.skill_engine
    monkeypatch.setattr(engine, "lookup_skill", lambda *a, **kw: {
        "game_id": "testgame", "is_curated": False, "steps": [
            {"action": "press_keys", "params": {"keys": ["space"]}},
            {"action": "press_keys", "params": {"keys": ["escape"]}},
        ], "post_verification": {"min_diff_ratio": .005},
    })
    keys, history, stats = [], [], []
    def press(**kwargs):
        keys.append(kwargs.get("target_window"))
        ctx.window_manager.set_mock_window(win)
        return SimpleNamespace(success=True, message="mock sent")
    monkeypatch.setattr(ctx.input_controller, "press_keys", press)
    monkeypatch.setattr(engine, "_record_execution_history", lambda *a, **kw: history.append(kw))
    monkeypatch.setattr(ctx.storage, "update_skill_stats", lambda *a, **kw: stats.append(kw))
    data = payload(await create_server(ctx).call_tool("execute_skill", {"skill_id": "test"}))
    assert not data["success"] and not data["post_verification_passed"]
    assert data["steps_executed"] == 1 and data["step_logs"][0]["executed"]
    assert keys == [win] and captures == ["native"] and not history and not stats


@pytest.mark.asyncio
async def test_selector_result_cannot_trigger_click_after_reattach(native, monkeypatch):
    ctx, win, captures = native
    engine = ctx.skill_engine
    monkeypatch.setattr(engine, "lookup_skill", lambda *a, **kw: {
        "game_id": "testgame", "is_curated": True, "steps": [
            {"action": "click_selector", "selector": "button"},
        ],
    })
    def resolve(**kwargs):
        assert kwargs["selector_game_id"] == "testgame"
        assert callable(kwargs["hover_reader"])
        ctx.window_manager.set_mock_window(win)
        return SimpleNamespace(status="found", coordinates_normalized=(.3, .4))
    monkeypatch.setattr(ctx.target_resolver, "resolve_target", resolve)
    monkeypatch.setattr(ctx.target_executor, "click_and_verify", lambda **kw: pytest.fail("stale click"))
    data = payload(await create_server(ctx).call_tool("execute_skill", {"skill_id": "test"}))
    assert not data["success"] and data["steps_executed"] == 0 and captures == ["native", "native"]


@pytest.mark.asyncio
async def test_skill_final_verification_cannot_commit_after_focus_loss(native, monkeypatch):
    ctx, win, captures = native
    engine = ctx.skill_engine
    monkeypatch.setattr(engine, "lookup_skill", lambda *a, **kw: {
        "game_id": "testgame", "is_curated": False, "steps": [
            {"action": "press_keys", "params": {"keys": ["space"]}},
        ], "post_verification": {"contains_text": "RESULT"},
    })
    monkeypatch.setattr(ctx.input_controller, "press_keys", lambda **kw: SimpleNamespace(success=True, message="sent"))
    def verify(**kwargs):
        ctx.window_manager.set_mock_window(replace(win, is_focused=False))
        return True, "looks like a result", {}
    monkeypatch.setattr(engine, "_verify_postconditions", verify)
    monkeypatch.setattr(engine, "_record_execution_history", lambda *a, **kw: pytest.fail("stale history"))
    monkeypatch.setattr(ctx.storage, "update_skill_stats", lambda *a, **kw: pytest.fail("stale stats"))
    data = payload(await create_server(ctx).call_tool("execute_skill", {"skill_id": "test"}))
    assert not data["success"] and not data["post_verification_passed"]
    assert data["steps_executed"] == 1 and captures == ["native", "native"]
