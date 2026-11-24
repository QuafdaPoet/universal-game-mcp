"""P0 regressions: synthetic actions cannot become live procedural knowledge."""

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from ugm.core.capture import CaptureManager, FixtureBackend
from ugm.core.types import WindowInfo
from ugm.memory.database import DatabaseManager
from ugm.server import UGMContext, create_server

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "hoi4_topbar.png"
STEPS = [{"action": "press_keys", "params": {"keys": ["space"]}}]


def context(monkeypatch, path, **kwargs):
    # Free of any real display capture, on both Linux and Windows CI.
    monkeypatch.setattr(CaptureManager, "_init_preferred_backend", lambda _: FixtureBackend())
    monkeypatch.setattr(
        "ugm.server.get_ocr_engine",
        lambda **_: SimpleNamespace(read_text=lambda *a, **kw: SimpleNamespace(full_text="", items=[])),
    )
    return UGMContext(db_path=path, **kwargs)


async def call(server, tool, args=None):
    res = await server.call_tool(tool, args or {})
    assert not res.is_error
    return res.structured_content.get("result", res.structured_content)


@pytest.mark.asyncio
async def test_fixture_never_reuses_or_mutates_live_database(tmp_path, monkeypatch):
    path = tmp_path / "memory.db"
    live = context(monkeypatch, path)
    live.storage.save_learned_skill(skill_id="live_only", game_id="hoi4", name="live", steps=STEPS)

    fixture = context(monkeypatch, path, offline_fixture=str(FIXTURE))
    server = create_server(fixture)
    assert await call(server, "list_learned_skills", {"game_id": "hoi4"}) == {"count": 0, "skills": []}
    candidate = await call(server, "learn_skill", {"skill_id": "fixture_only", "name": "fixture", "steps": STEPS})
    assert candidate["success"]
    assert live.storage.get_learned_skill("fixture_only") is None
    assert live.storage.get_learned_skill("live_only") is not None

    resumed = context(monkeypatch, path, offline_fixture=str(FIXTURE))
    assert resumed.storage.get_learned_skill("fixture_only").source == "synthetic_fixture"


@pytest.mark.asyncio
async def test_forced_fixture_probe_has_no_empirical_success(tmp_path, monkeypatch):
    ctx = context(monkeypatch, tmp_path / "memory.db", offline_fixture=str(FIXTURE))

    def refuse_capture(**kwargs):
        pytest.fail("synthetic probe captured a frame as post-action evidence")
    monkeypatch.setattr(ctx.capture_manager, "capture", refuse_capture)
    result = await call(create_server(ctx), "probe_action", {
        "action": "press_keys", "params": {"keys": ["space"]}, "dry_run": False,
    })
    assert result["dry_run"]
    assert result["evidence_level"] == "simulated"
    assert result["simulation_accepted"]
    assert not result["executed"]
    assert not result["verification_passed"]
    assert not result["visual_change_detected"]
    assert result["diff_ratio"] == 0.0
    assert result["new_text_detected"] == []


def test_per_call_dry_run_preserves_live_history_skill_and_navigation(tmp_path, monkeypatch):
    ctx = context(monkeypatch, tmp_path / "memory.db")
    # Bind independent test frame/window doubles without entering fixture session mode.
    ctx.window_manager.set_mock_window(WindowInfo(
        hwnd=731, title="Unit test", process_name="ugm_unit_test.exe", pid=1,
        x=0, y=0, width=128, height=128, client_x=0, client_y=0,
        client_width=128, client_height=128, is_minimized=False, is_focused=True,
    ))
    monkeypatch.setattr(ctx.capture_manager, "capture", lambda **kwargs: np.zeros((128, 128, 3), dtype=np.uint8))
    monkeypatch.setattr(ctx.ocr_engine, "read_text", lambda *a, **kw: SimpleNamespace(full_text="150", items=[]))
    monkeypatch.setattr(ctx.input_controller, "check_corner_abort", lambda: None)

    ctx.skill_learner.learn_candidate_skill(
        "hoi4", "live_candidate", "candidate", STEPS,
        post_verification={"expected_text": "a live result"},
    )
    before = ctx.storage.get_learned_skill("live_candidate")
    result = ctx.skill_learner.validate_skill("live_candidate", game_id="hoi4", dry_run=True)
    assert result.success and result.dry_run
    assert not result.post_verification_passed

    graph = ctx.get_navigation_graph("hoi4")
    edge = graph.plan_route("main_map", "production").edges[0]
    before_cost = edge.cost
    nav = ctx.get_navigator("hoi4").navigate_to("production", dry_run=True)
    assert nav.success and nav.dry_run
    assert graph.plan_route("main_map", "production").edges[0].cost == before_cost

    result = ctx.explorer.probe_action("press_keys", {"keys": ["space"]}, dry_run=True)
    assert result["evidence_level"] == "simulated"
    assert ctx.storage.get_learned_skill("live_candidate") == before
    assert ctx.storage.get_action_history("hoi4") == []
    assert ctx.storage.get_states("hoi4") == []
    assert ctx.storage.get_transitions("hoi4") == []


@pytest.mark.asyncio
async def test_dry_run_click_does_not_focus_or_verify(tmp_path, monkeypatch):
    ctx = context(monkeypatch, tmp_path / "memory.db", offline_fixture=str(FIXTURE))
    ctx.window_manager.current_window.is_focused = False

    def unexpected(*args, **kwargs):
        pytest.fail("dry-run click attempted focus or grabbed a verification frame")

    monkeypatch.setattr(ctx.window_manager, "focus_window", unexpected)
    monkeypatch.setattr(ctx.capture_manager, "capture", unexpected)
    server = create_server(ctx)
    result = await call(server, "click_target", {"x": 0.4, "y": 0.4, "dry_run": False})
    assert result["dry_run"]
    assert not result["executed"]
    assert not result["verification_passed"]
    assert result["evidence"]["evidence_level"] == "simulated"
    assert result["evidence"]["simulation_accepted"]

    rejected = await call(server, "click_target", {"x": 2.0, "y": 0.4, "dry_run": False})
    assert rejected["status"] == "failed"
    assert not rejected["evidence"]["simulation_accepted"]


def test_server_dry_run_has_separate_knowledge_namespace(tmp_path, monkeypatch):
    path = tmp_path / "memory.db"
    ctx = context(monkeypatch, path, enforce_dry_run=True)
    assert ctx.input_controller.is_dry_run_active(False)
    ctx.storage.register_game("demo", "Demo")
    ctx.skill_learner.learn_candidate_skill("demo", "only_dry", "demo", STEPS)
    ctx.storage.db.close()
    assert not path.exists()
    live = context(monkeypatch, path)
    assert live.storage.get_learned_skill("only_dry") is None


def test_live_refuses_copied_simulation_database(tmp_path):
    sim = DatabaseManager(tmp_path / "memory.db.simulation", simulation=True)
    clone = tmp_path / "renamed.db"
    import sqlite3

    with sqlite3.connect(clone) as destination:
        sim.get_connection().backup(destination)
    sim.close()
    with pytest.raises(ValueError, match="simulation database cannot be opened as live"):
        DatabaseManager(clone)


def test_probe_failure_does_not_guess_escape_is_undo(tmp_path, monkeypatch):
    ctx = context(monkeypatch, tmp_path / "memory.db")
    frame = np.zeros((64, 64, 3), dtype=np.uint8)
    monkeypatch.setattr(ctx.capture_manager, "capture", lambda **kwargs: frame)

    def unexpected(*args, **kwargs):
        pytest.fail("Probe dispatched an unverified Escape retreat")

    monkeypatch.setattr(ctx.input_controller, "press_keys", unexpected)
    result = ctx.explorer.probe_action("wait", {"duration_s": 0.05}, revert_on_fail=True)
    assert not result["reverted"]
    assert result["revert_status"] == "not_supported_without_verified_undo"
