"""
tests/test_v2_autonomous_exploration.py
Synthetic integration tests for Universal Game MCP (UGM) V2:
Autonomous UI exploration, empty-start bootstrap, Observe-Act-Verify probing,
4-state knowledge lifecycle, checkpoint rollback, gradual optimization,
cross-game concepts, and bounded resource pruning.
"""

import json
import os
import time
import pytest
import numpy as np
import cv2

from ugm.server import create_server, UGMContext
from ugm.core.safety import safety_manager
from ugm.core.types import NormalizedRect, WindowInfo
from ugm.memory.database import DatabaseManager
from ugm.memory.storage import PersistentKnowledgeStorage
from ugm.core.explorer import UIExplorer, UIElementCandidate


def extract_data(res):
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
def v2_test_env(tmp_path):
    """
    Sets up an isolated V2 environment with temporary SQLite DB and mock fixture frame.
    """
    safety_manager.reset_emergency_stop()
    db_file = tmp_path / "v2_test_memory.db"
    fixture_path = "tests/fixtures/hoi4_topbar.png"

    ctx = UGMContext(offline_fixture=fixture_path, db_path=str(db_file))
    ctx.input_controller.dry_run_default = True
    server = create_server(ctx)

    return {
        "ctx": ctx,
        "server": server,
        "db_file": str(db_file),
        "db_mgr": ctx.storage.db,
        "storage": ctx.storage,
        "explorer": ctx.explorer
    }


# ============================================================================
# AC-1: Unknown Game Discovery & Empty Knowledge Bootstrap
# ============================================================================

@pytest.mark.asyncio
async def test_ac1_unknown_game_discovery_and_empty_bootstrap(v2_test_env):
    """
    AC-1: AI connects to an unknown, previously unsupported offline game.
    Discovers window, creates an initially empty per-game GameSkill memory,
    and returns empty knowledge baseline without hardcoded game logic.
    """
    server = v2_test_env["server"]
    storage = v2_test_env["storage"]

    # Call discover_and_attach_game with a new game title
    res = await server.call_tool("discover_and_attach_game", {
        "window_title": "Grand Strategy Nova",
        "process_name": "gs_nova.exe",
        "auto_detect_foreground": True
    })
    data = extract_data(res)

    assert data["status"] == "attached"
    game_id = data["game_id"]
    assert game_id == "gs_nova"
    assert data["is_new_game"] is True
    assert data["learned_skills_count"] == 0
    assert data["learned_states_count"] == 0

    # Verify database persistence: Game exists but has empty knowledge
    game = storage.get_game("gs_nova")
    assert game is not None
    assert game["name"] == "Grand Strategy Nova"

    skills = storage.get_learned_skills(game_id="gs_nova")
    assert len(skills) == 0, "GameSkill memory must start completely empty for a new game"

    ui_states = storage.get_ui_states(game_id="gs_nova")
    assert len(ui_states) == 0, "UI state memory must start empty"


# ============================================================================
# AC-2: Adaptive UI Candidate Discovery
# ============================================================================

@pytest.mark.asyncio
async def test_ac2_adaptive_ui_candidate_discovery(v2_test_env):
    """
    AC-2: AI observes previously unseen game UI and detects interactive candidate elements
    (buttons, tabs, prominent text anchors) with normalized bounding coordinates.
    """
    server = v2_test_env["server"]

    res = await server.call_tool("explore_interactive_elements", {
        "scan_mode": "all"
    })
    data = extract_data(res)

    assert data["count"] > 0
    candidates = data["candidates"]
    assert len(candidates) >= 1

    # Verify structured attributes of candidates
    first = candidates[0]
    assert "element_id" in first
    assert "affordance_type" in first
    assert "bbox_normalized" in first
    assert "center_normalized" in first
    assert "confidence" in first

    bbox = first["bbox_normalized"]
    assert 0.0 <= bbox["x"] <= 1.0
    assert 0.0 <= bbox["y"] <= 1.0
    assert 0.0 < bbox["width"] <= 1.0
    assert 0.0 < bbox["height"] <= 1.0


# ============================================================================
# AC-3: Observe-Act-Verify Probe Step & Safe Retreat
# ============================================================================

@pytest.mark.asyncio
async def test_ac3_probe_action_observe_act_verify_and_retreat(v2_test_env):
    """
    AC-3: AI executes a bounded Observe-Act-Verify probe step.
    Measures visual state change and provides empirical verification evidence.
    Supports optional revert_on_fail safe retreat (e.g. Esc key).
    """
    server = v2_test_env["server"]

    # 1. Successful dry-run probe click
    probe_res = await server.call_tool("probe_action", {
        "action": "click",
        "params": {"x": 0.5, "y": 0.5, "button": "left"},
        "dry_run": True,
        "revert_on_fail": False
    })
    data = extract_data(probe_res)
    assert data["executed"] is True
    assert data["action"] == "click"
    assert "diff_ratio" in data
    assert "empirical_evidence" in data
    assert data["verification_passed"] is True

    # 2. Key press probe step with revert_on_fail enabled
    key_probe = await server.call_tool("probe_action", {
        "action": "press_key",
        "params": {"key": "space"},
        "dry_run": True,
        "revert_on_fail": True
    })
    key_data = extract_data(key_probe)
    assert key_data["executed"] is True
    assert key_data["action"] == "press_key"


# ============================================================================
# AC-4: Dynamic Skill Learning, Restart Resumption & Cross-Session Persistence
# ============================================================================

@pytest.mark.asyncio
async def test_ac4_dynamic_skill_learning_and_cross_session_persistence(v2_test_env):
    """
    AC-4: AI dynamically synthesizes and records a new GameSkill from an empty state.
    Simulates application restart; verifies skills and knowledge resume cleanly across sessions.
    """
    server = v2_test_env["server"]
    db_file = v2_test_env["db_file"]
    game_id = "civ_space"

    # Register game
    await server.call_tool("discover_and_attach_game", {
        "window_title": "Civilization in Space",
        "process_name": "civ_space.exe"
    })

    # AI synthesizes and learns a new skill
    learn_res = await server.call_tool("learn_skill", {
        "game_id": game_id,
        "skill_id": "open_tech_tree",
        "name": "Open Technology Tree",
        "description": "Presses T or clicks beaker to open technology research view",
        "steps": [
            {"action": "press_key", "params": {"key": "t"}, "wait_after_s": 0.2}
        ],
        "initial_confidence": 0.75,
        "tags": ["navigation", "research"]
    })
    data = extract_data(learn_res)
    assert data["status"] == "candidate"
    assert data["skill_id"] == "open_tech_tree"

    # Simulate full process / MCP server restart:
    # Instantiate completely new UGMContext and server pointing to the exact same SQLite database
    new_ctx = UGMContext(offline_fixture="tests/fixtures/hoi4_topbar.png", db_path=db_file)
    new_server = create_server(new_ctx)

    # Resume session: Query learned skills
    list_res = await new_server.call_tool("list_learned_skills", {"game_id": game_id})
    list_data = extract_data(list_res)

    assert list_data["count"] >= 1
    found_skills = {s["skill_id"]: s for s in list_data["skills"]}
    assert "open_tech_tree" in found_skills
    resumed_skill = found_skills["open_tech_tree"]
    assert resumed_skill["name"] == "Open Technology Tree"
    assert resumed_skill["status"] == "candidate"
    assert resumed_skill["success_count"] == 1


# ============================================================================
# AC-5: Isolated Execution Failure Resilience
# ============================================================================

def test_ac5_isolated_execution_failure_preserves_verified_status(v2_test_env):
    """
    AC-5: A previously verified skill must NEVER be demoted by an isolated transient
    execution failure (e.g. window unfocused, mouse out of bounds).
    Distinguishes execution errors from false learned rules.
    """
    storage = v2_test_env["storage"]
    game_id = "stellar_strategy"
    skill_id = "toggle_pause_cmd"

    # Setup game and verified skill with 5 successes
    storage.register_game(game_id, "Stellar Strategy")
    storage.save_learned_skill(
        game_id=game_id,
        skill_id=skill_id,
        name="Toggle Pause",
        description="Presses spacebar",
        steps=[{"action": "press_key", "params": {"key": "space"}}],
        initial_confidence=0.95
    )

    # Promote to verified state
    storage.update_knowledge_state(game_id=game_id, item_type="skill", item_id=skill_id, new_status="verified")
    for _ in range(5):
        storage.update_skill_stats(skill_id=skill_id, success=True, latency_ms=10.0)

    skill_before = storage.get_learned_skills(game_id=game_id)[0]
    assert skill_before["status"] == "verified"
    assert skill_before["success_count"] == 6
    assert skill_before["consecutive_failures"] == 0

    # Simulate TRANSIENT EXECUTION FAILURE (e.g. window minimized / timeout)
    # is_execution_error=True must NOT penalize or demote verified knowledge
    storage.update_skill_stats(skill_id=skill_id, success=False, latency_ms=50.0, is_execution_error=True)

    skill_after = storage.get_learned_skills(game_id=game_id)[0]
    assert skill_after["status"] == "verified", "Verified skill must preserve verified status on transient execution error"
    assert skill_after["consecutive_failures"] == 0, "Transient execution errors must not increment consecutive rule failures"
    assert skill_after["failure_count"] == 1


# ============================================================================
# AC-6: Suspect State Demotion After 2 Consecutive Postcondition Failures
# ============================================================================

def test_ac6_two_consecutive_postcondition_failures_demotes_to_suspect(v2_test_env):
    """
    AC-6: When postcondition verification fails repeatedly (rule is actually false):
    - 1 isolated postcondition failure preserves verified status (consecutive_failures=1).
    - 2 consecutive postcondition failures automatically demotes skill to 'suspect'.
    """
    storage = v2_test_env["storage"]
    game_id = "space_sim"
    skill_id = "dock_ship"

    storage.register_game(game_id, "Space Sim")
    storage.save_learned_skill(
        game_id=game_id,
        skill_id=skill_id,
        name="Dock Ship",
        description="Docks ship at station",
        steps=[{"action": "press_key", "params": {"key": "d"}}],
        initial_confidence=0.90
    )
    storage.update_knowledge_state(game_id=game_id, item_type="skill", item_id=skill_id, new_status="verified")

    # Failure 1 (Rule postcondition failed, NOT execution error)
    storage.update_skill_stats(skill_id=skill_id, success=False, latency_ms=20.0, is_execution_error=False)
    s1 = storage.get_learned_skills(game_id=game_id)[0]
    assert s1["status"] == "verified", "First failure gives grace period; remains verified"
    assert s1["consecutive_failures"] == 1

    # Failure 2 (Second consecutive postcondition failure)
    storage.update_skill_stats(skill_id=skill_id, success=False, latency_ms=25.0, is_execution_error=False)
    s2 = storage.get_learned_skills(game_id=game_id)[0]
    assert s2["status"] == "suspect", "Two consecutive verified postcondition failures must demote skill to suspect"
    assert s2["consecutive_failures"] == 2
    assert s2["confidence"] < 0.90


# ============================================================================
# AC-7: Checkpoint Creation, Corruption Detection & Atomic Rollback
# ============================================================================

@pytest.mark.asyncio
async def test_ac7_checkpoint_snapshot_and_atomic_rollback(v2_test_env):
    """
    AC-7: AI creates an atomic point-in-time knowledge checkpoint.
    When subsequent exploration results in corrupted memory or false inferences,
    AI rolls back to the checkpoint, restoring pristine state.
    """
    server = v2_test_env["server"]
    storage = v2_test_env["storage"]
    game_id = "ancient_empires"

    # 1. Establish valid initial state with 2 verified skills
    storage.register_game(game_id, "Ancient Empires")
    storage.save_learned_skill(game_id, "skill_a", "Skill A", "Base valid skill A", [])
    storage.save_learned_skill(game_id, "skill_b", "Skill B", "Base valid skill B", [])

    # 2. Take atomic checkpoint snapshot
    cp_res = await server.call_tool("create_knowledge_checkpoint", {
        "game_id": game_id,
        "label": "golden_master_v1",
        "description": "Baseline valid exploration state"
    })
    cp_data = extract_data(cp_res)
    assert cp_data["success"] is True
    checkpoint_id = cp_data["checkpoint_id"]

    # 3. Simulate memory corruption: False inferences / bad skills injected
    storage.save_learned_skill(game_id, "corrupted_skill_x", "Corrupt X", "Erroneous hallucinated skill", [])
    storage.save_learned_skill(game_id, "corrupted_skill_y", "Corrupt Y", "Erroneous hallucinated skill", [])
    assert len(storage.get_learned_skills(game_id)) == 4

    # 4. Rollback to checkpoint snapshot
    rb_res = await server.call_tool("rollback_knowledge_checkpoint", {
        "game_id": game_id,
        "checkpoint_id": checkpoint_id
    })
    rb_data = extract_data(rb_res)
    assert rb_data["success"] is True

    # 5. Verify restored state: Only pristine original skills exist
    restored_skills = storage.get_learned_skills(game_id)
    assert len(restored_skills) == 2
    restored_ids = {s["skill_id"] for s in restored_skills}
    assert restored_ids == {"skill_a", "skill_b"}
    assert "corrupted_skill_x" not in restored_ids
    assert "corrupted_skill_y" not in restored_ids


# ============================================================================
# AC-8: Gradual Skill Optimization & Version Bump
# ============================================================================

@pytest.mark.asyncio
async def test_ac8_gradual_skill_optimization(v2_test_env):
    """
    AC-8: AI optimizes an existing learned GameSkill by refining its step sequence
    (e.g. shortening delays, removing redundant clicks), recording rationale,
    and bumping its version while preserving historical execution track records.
    """
    server = v2_test_env["server"]
    storage = v2_test_env["storage"]
    game_id = "turn_based_4x"
    skill_id = "recruit_settler"

    storage.register_game(game_id, "Turn Based 4X")
    storage.save_learned_skill(
        game_id=game_id,
        skill_id=skill_id,
        name="Recruit Settler",
        description="Recruits a settler unit",
        steps=[
            {"action": "click", "params": {"x": 0.1, "y": 0.1}, "wait_after_s": 1.0},
            {"action": "click", "params": {"x": 0.1, "y": 0.1}, "wait_after_s": 0.5},
            {"action": "press_key", "params": {"key": "enter"}, "wait_after_s": 0.5}
        ],
        initial_confidence=0.85
    )
    # Log 3 successful executions
    for _ in range(3):
        storage.update_skill_stats(skill_id=skill_id, success=True, latency_ms=15.0)

    # AI calls optimize_skill with streamlined step sequence
    optimized_steps = [
        {"action": "click", "params": {"x": 0.1, "y": 0.1}, "wait_after_s": 0.2},
        {"action": "press_key", "params": {"key": "enter"}, "wait_after_s": 0.2}
    ]
    opt_res = await server.call_tool("optimize_skill", {
        "skill_id": skill_id,
        "steps": optimized_steps,
        "rationale": "Removed redundant double click and reduced stabilization delay from 1.0s to 0.2s"
    })
    opt_data = extract_data(opt_res)
    assert opt_data["success"] is True

    # Verify updated skill in storage
    skill = storage.get_learned_skills(game_id=game_id)[0]
    assert skill["version"] == "1.1.0", "Optimization must bump minor version"
    assert len(skill["steps"]) == 2, "Steps must reflect streamlined sequence"
    assert skill["success_count"] == 4, "Execution track record must be preserved"
    assert "redundant" in skill["optimization_notes"].lower()


# ============================================================================
# AC-9: Cross-Game Concepts Query & Exploration Priors
# ============================================================================

@pytest.mark.asyncio
async def test_ac9_cross_game_concepts_query(v2_test_env):
    """
    AC-9: AI queries universal cross-game heuristics (speed control, pause toggle, dialog cancel)
    to seed exploration priors without per-game hardcoded logic.
    """
    server = v2_test_env["server"]

    # 1. Query all concepts
    res_all = await server.call_tool("query_cross_game_concepts", {})
    data_all = extract_data(res_all)
    assert data_all["count"] >= 5

    concept_ids = {c["concept_id"] for c in data_all["concepts"]}
    assert "pause_toggle" in concept_ids
    assert "cancel_dialog" in concept_ids
    assert "game_speed" in concept_ids

    # 2. Query filtered by category
    res_nav = await server.call_tool("query_cross_game_concepts", {"category": "navigation"})
    data_nav = extract_data(res_nav)
    assert data_nav["count"] >= 1
    for c in data_nav["concepts"]:
        assert c["category"] == "navigation"


# ============================================================================
# AC-10: Bounded Exploration Safeguards & Temporary Data Pruning
# ============================================================================

@pytest.mark.asyncio
async def test_ac10_bounded_exploration_and_temporary_data_pruning(v2_test_env, tmp_path):
    """
    AC-10: Verifies bounded storage safeguards.
    Temporary screenshots and redundant action history entries are automatically pruned.
    """
    server = v2_test_env["server"]
    storage = v2_test_env["storage"]
    conn = storage.db.get_connection()
    game_id = "tactical_ops"

    storage.register_game(game_id, "Tactical Ops")

    # Insert 15 action history records
    with conn:
        for i in range(15):
            conn.execute("""
                INSERT INTO action_history (game_id, action_name, success, duration_ms, created_at)
                VALUES (?, ?, 1, 10.0, ?)
            """, (game_id, f"btn_{i}", time.time() - (15 - i)))

    # Create dummy screenshot files in storage/screenshots
    shots_dir = tmp_path / "screenshots"
    shots_dir.mkdir(parents=True, exist_ok=True)
    f1 = shots_dir / "old_shot.png"
    f2 = shots_dir / "recent_shot.png"
    f1.write_bytes(b"dummy_png_bytes")
    f2.write_bytes(b"dummy_png_bytes")
    # Make f1 appear old
    old_time = time.time() - 7200
    os.utime(f1, (old_time, old_time))

    # AI calls prune_temporary_data with max_history_records=5
    prune_res = await server.call_tool("prune_temporary_data", {
        "game_id": game_id,
        "max_history_records": 5,
        "screenshot_dir": str(shots_dir)
    })
    prune_data = extract_data(prune_res)

    assert prune_data["pruned_screenshots"] == 1
    assert prune_data["pruned_history_records"] == 10

    # Verify only 5 most recent records remain
    cur = conn.execute("SELECT COUNT(*) FROM action_history WHERE game_id = ?;", (game_id,))
    count = cur.fetchone()[0]
    assert count == 5


# ============================================================================
# Additional Comprehensive V2 Edge Case & Hardening Tests
# ============================================================================

@pytest.mark.asyncio
async def test_ac1_window_manager_attachment_and_active_game_id(v2_test_env):
    """
    Verifies that discover_and_attach_game binds the active game window in WindowManager,
    and subsequent tool calls like learn_skill default to the active attached game.
    """
    server = v2_test_env["server"]
    ctx = v2_test_env["ctx"]

    attach_res = await server.call_tool("discover_and_attach_game", {
        "window_title": "Endless Space",
        "process_name": "endless_space.exe"
    })
    attach_data = extract_data(attach_res)
    assert attach_data["status"] == "attached"
    assert attach_data["game_id"] == "endless_space"

    # Verify WindowManager current window is updated
    assert ctx.window_manager.current_window is not None
    assert "endless space" in ctx.window_manager.current_window.title.lower()

    # Learn skill without explicitly passing game_id
    learn_res = await server.call_tool("learn_skill", {
        "skill_id": "end_turn_btn",
        "name": "End Turn",
        "steps": [{"action": "press_key", "params": {"key": "enter"}}]
    })
    learn_data = extract_data(learn_res)
    assert learn_data["success"] is True
    assert learn_data["game_id"] == "endless_space"


@pytest.mark.asyncio
async def test_ac2_contour_and_shape_candidates(v2_test_env):
    """
    Verifies interactive element discovery with scan_mode='shapes' and fallback anchor.
    """
    server = v2_test_env["server"]

    res_shapes = await server.call_tool("explore_interactive_elements", {
        "scan_mode": "shapes"
    })
    data = extract_data(res_shapes)
    assert data["count"] >= 1
    assert data["scan_mode"] == "shapes"


@pytest.mark.asyncio
async def test_ac3_probe_action_safety_and_emergency_stop(v2_test_env):
    """
    Verifies probe_action safety rules:
    - Coordinates out of bounds [0.0, 1.0] raise SafetyViolationError.
    - EmergencyStopTriggered is never swallowed as an ordinary execution error.
    """
    from ugm.core.safety import SafetyViolationError, EmergencyStopTriggered

    server = v2_test_env["server"]

    # 1. Coordinate out of bounds
    with pytest.raises(SafetyViolationError):
        await server.call_tool("probe_action", {
            "action": "click",
            "params": {"x": 1.5, "y": 0.5}
        })

    # 2. Emergency stop active
    safety_manager.trigger_emergency_stop("Test emergency stop abort")
    try:
        with pytest.raises(EmergencyStopTriggered):
            await server.call_tool("probe_action", {
                "action": "press_key",
                "params": {"key": "space"}
            })
    finally:
        safety_manager.reset_emergency_stop()


@pytest.mark.asyncio
async def test_ac3_probe_action_budget_and_action_history(v2_test_env):
    """
    Verifies probe action budget enforcement (bounded exploration) and action history recording.
    """
    from ugm.core.safety import SafetyViolationError

    server = v2_test_env["server"]
    explorer = v2_test_env["explorer"]
    storage = v2_test_env["storage"]

    # Set small probe budget
    explorer.reset_probe_budget()
    explorer.max_probe_depth = 3

    # Execute 3 probes
    for i in range(3):
        res = await server.call_tool("probe_action", {
            "action": "press_key",
            "params": {"key": "space"},
            "dry_run": True
        })
        d = extract_data(res)
        assert d["executed"] is True
        assert d["probe_step"] == i + 1

    # 4th probe exceeds budget
    with pytest.raises(SafetyViolationError):
        await server.call_tool("probe_action", {
            "action": "press_key",
            "params": {"key": "space"},
            "dry_run": True
        })

    # Reset budget for further tests
    explorer.max_probe_depth = 100
    explorer.reset_probe_budget()

    # Verify action history records in storage
    history = storage.get_action_history("hoi4")
    assert len(history) >= 3
    assert any("probe_press_key" in h["action_name"] for h in history)


def test_ac5_verified_skill_protection_on_candidate_resave(v2_test_env):
    """
    Verifies that calling save_learned_skill for an already verified skill
    with candidate status does NOT overwrite or demote its status or confidence.
    """
    storage = v2_test_env["storage"]
    game_id = "shield_game"
    skill_id = "fortify_city"

    storage.register_game(game_id, "Shield Game")
    storage.save_learned_skill(
        game_id=game_id,
        skill_id=skill_id,
        name="Fortify City",
        steps=[{"action": "press_key", "params": {"key": "f"}}],
        initial_confidence=0.92
    )
    storage.update_knowledge_state(game_id=game_id, item_type="skill", item_id=skill_id, new_status="verified")

    # Verify skill is verified with confidence
    sk1 = storage.get_learned_skills(game_id)[0]
    assert sk1["status"] == "verified"
    assert sk1["confidence"] == 0.92

    # Re-save with candidate status and default confidence 0.5
    storage.save_learned_skill(
        game_id=game_id,
        skill_id=skill_id,
        name="Fortify City Updated",
        steps=[{"action": "press_key", "params": {"key": "f"}}],
        status="candidate",
        confidence=0.50
    )

    # Invariant check: Status remains verified and confidence is not wiped
    sk2 = storage.get_learned_skills(game_id)[0]
    assert sk2["status"] == "verified", "Verified skill must not be demoted to candidate on re-save"
    assert sk2["confidence"] == 0.92, "Verified skill confidence must not be overwritten by candidate default"


def test_ac6_transition_lifecycle_and_execution_error(v2_test_env):
    """
    Verifies 4-state lifecycle on navigation edges:
    - Transient execution error (is_execution_error=True) does not increment failures or demote.
    - 2 consecutive postcondition failures demotes a verified transition to suspect.
    """
    storage = v2_test_env["storage"]
    game_id = "rts_command"

    storage.register_game(game_id, "RTS Command")
    # Save verified transition
    storage.save_transition(
        game_id=game_id,
        from_state="base_view",
        to_state="barracks_menu",
        action_type="click",
        params={"x": 0.2, "y": 0.3},
        success=True,
        status="verified"
    )

    t0 = storage.get_transitions(game_id)[0]
    assert t0.status == "verified"
    assert t0.consecutive_failures == 0

    # 1. Transient execution error -> must not demote
    storage.save_transition(
        game_id=game_id,
        from_state="base_view",
        to_state="barracks_menu",
        action_type="click",
        params={"x": 0.2, "y": 0.3},
        success=False,
        is_execution_error=True
    )
    t1 = storage.get_transitions(game_id)[0]
    assert t1.status == "verified"
    assert t1.consecutive_failures == 0

    # 2. First postcondition failure -> retains verified status (grace)
    storage.save_transition(
        game_id=game_id,
        from_state="base_view",
        to_state="barracks_menu",
        action_type="click",
        params={"x": 0.2, "y": 0.3},
        success=False,
        is_execution_error=False
    )
    t2 = storage.get_transitions(game_id)[0]
    assert t2.status == "verified"
    assert t2.consecutive_failures == 1

    # 3. Second consecutive postcondition failure -> transitions to suspect
    storage.save_transition(
        game_id=game_id,
        from_state="base_view",
        to_state="barracks_menu",
        action_type="click",
        params={"x": 0.2, "y": 0.3},
        success=False,
        is_execution_error=False
    )
    t3 = storage.get_transitions(game_id)[0]
    assert t3.status == "suspect"
    assert t3.consecutive_failures == 2


def test_ac6_suspect_recovery_requires_two_successes(v2_test_env):
    """
    Verifies that a suspect skill requires 2 consecutive successful executions
    to be restored to verified status (1 success keeps it suspect, 2nd restores it).
    """
    storage = v2_test_env["storage"]
    game_id = "recover_game"
    skill_id = "warp_drive"

    storage.register_game(game_id, "Recover Game")
    storage.save_learned_skill(
        game_id=game_id,
        skill_id=skill_id,
        name="Warp Drive",
        steps=[{"action": "press_key", "params": {"key": "w"}}],
        initial_confidence=0.9
    )
    storage.update_knowledge_state(game_id, "skill", skill_id, "verified")

    # Demote to suspect with 2 consecutive failures
    storage.update_skill_stats(skill_id, success=False, is_execution_error=False)
    storage.update_skill_stats(skill_id, success=False, is_execution_error=False)
    s_suspect = storage.get_learned_skills(game_id)[0]
    assert s_suspect["status"] == "suspect"

    # 1st success: remains suspect
    storage.update_skill_stats(skill_id, success=True, is_execution_error=False)
    s_rec1 = storage.get_learned_skills(game_id)[0]
    assert s_rec1["status"] == "suspect", "1 single success must not prematurely restore suspect skill"

    # 2nd consecutive success: restored to verified
    storage.update_skill_stats(skill_id, success=True, is_execution_error=False)
    s_rec2 = storage.get_learned_skills(game_id)[0]
    assert s_rec2["status"] == "verified", "2 consecutive successes must restore suspect skill to verified"


@pytest.mark.asyncio
async def test_ac9_register_cross_game_concept_via_mcp(v2_test_env):
    """
    Verifies that connected AI can register a newly discovered universal cross-game concept via MCP.
    """
    server = v2_test_env["server"]

    reg_res = await server.call_tool("register_cross_game_concept", {
        "concept_id": "quick_save",
        "category": "system",
        "name": "Quick Save",
        "description": "Standard hotkey to create quick save point",
        "default_action": "press_keys",
        "default_params": {"keys": ["f5"]},
        "typical_effects": {"save": "created"}
    })
    reg_data = extract_data(reg_res)
    assert reg_data["success"] is True
    assert reg_data["concept_id"] == "quick_save"

    # Query back via MCP
    query_res = await server.call_tool("query_cross_game_concepts", {"category": "system"})
    q_data = extract_data(query_res)
    concept_ids = {c["concept_id"] for c in q_data["concepts"]}
    assert "quick_save" in concept_ids


@pytest.mark.asyncio
async def test_ac10_prune_temporary_data_sandbox_security(v2_test_env):
    """
    Verifies path traversal / sandbox protection in prune_temporary_data tool.
    Passing a directory outside workspace/temp root must raise SafetyViolationError.
    """
    from ugm.core.safety import SafetyViolationError

    server = v2_test_env["server"]

    # Traversal escape
    with pytest.raises(SafetyViolationError):
        await server.call_tool("prune_temporary_data", {
            "screenshot_dir": "../../../escaped_dir"
        })

    # Absolute path outside sandbox
    with pytest.raises(SafetyViolationError):
        await server.call_tool("prune_temporary_data", {
            "screenshot_dir": "/etc"
        })


def test_adaptive_ui_anchor_matching(v2_test_env):
    """
    Verifies that generic UI layout scanning properly captures prominent_text into evidence
    and matches it against registered states on subsequent scans.
    """
    ctx = v2_test_env["ctx"]
    storage = v2_test_env["storage"]
    game_id = "adaptive_title"

    storage.register_game(game_id, "Adaptive Title")
    # Record known state with prominent text evidence
    storage.record_state(
        game_id=game_id,
        state_name="research_hub",
        phash="0000000000000000",
        confidence=0.85,
        evidence={"prominent_text": ["Treaty", "Munich", "INFANTRY"]}
    )

    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
    det = ctx.state_recognizer.recognize_state(frame, game_id=game_id)
    assert det.state_name == "research_hub"
    assert det.confidence >= 0.70
    assert "anchor_match" in det.signals


@pytest.mark.asyncio
async def test_manage_knowledge_state_mcp_and_failure_counter_reset(v2_test_env):
    """
    Verifies manage_knowledge_state MCP tool updates knowledge lifecycle,
    and promoting a suspect/candidate skill to verified resets consecutive_failures to 0.
    """
    server = v2_test_env["server"]
    storage = v2_test_env["storage"]
    game_id = "gov_game"

    storage.register_game(game_id, "Governance Game")
    storage.save_learned_skill(
        game_id=game_id,
        skill_id="jump_drive",
        name="Jump Drive",
        steps=[{"action": "press_key", "params": {"key": "j"}}],
        initial_confidence=0.9
    )
    storage.update_knowledge_state(game_id, "skill", "jump_drive", "verified")

    # Induce 2 failures on verified skill -> consecutive_failures = 2, status = suspect
    storage.update_skill_stats(skill_id="jump_drive", success=False, is_execution_error=False)
    storage.update_skill_stats(skill_id="jump_drive", success=False, is_execution_error=False)
    s_before = storage.get_learned_skills(game_id)[0]
    assert s_before["status"] == "suspect"
    assert s_before["consecutive_failures"] == 2

    # Agent verifies jump_drive via manage_knowledge_state MCP tool
    res = await server.call_tool("manage_knowledge_state", {
        "game_id": game_id,
        "item_type": "skill",
        "item_id": "jump_drive",
        "status": "verified",
        "reason": "Empirically verified by external agent"
    })
    data = extract_data(res)
    assert data["success"] is True
    assert data["status"] == "verified"

    # Verify consecutive_failures was reset to 0
    s_after = storage.get_learned_skills(game_id)[0]
    assert s_after["status"] == "verified"
    assert s_after["consecutive_failures"] == 0


@pytest.mark.asyncio
async def test_rejected_skill_execution_blocked(v2_test_env):
    """
    Verifies that a skill marked as 'rejected' cannot be executed and fails fast.
    """
    server = v2_test_env["server"]
    storage = v2_test_env["storage"]
    game_id = "reject_game"
    skill_id = "falsified_exploit"

    storage.register_game(game_id, "Reject Game")
    storage.save_learned_skill(
        game_id=game_id,
        skill_id=skill_id,
        name="Falsified Exploit",
        steps=[{"action": "press_key", "params": {"key": "f12"}}],
        initial_confidence=0.5
    )

    # Mark rejected
    await server.call_tool("manage_knowledge_state", {
        "game_id": game_id,
        "item_type": "skill",
        "item_id": skill_id,
        "status": "rejected",
        "reason": "False correlation detected during exploration"
    })

    # Attempt to execute skill
    exec_res = await server.call_tool("execute_skill", {
        "game_id": game_id,
        "skill_id": skill_id,
        "dry_run": True
    })
    exec_data = extract_data(exec_res)
    assert exec_data["success"] is False
    assert exec_data["status"] == "rejected"
    assert "REJECTED" in exec_data["message"]


def test_rejected_transitions_excluded_from_dijkstra(v2_test_env):
    """
    Verifies that transitions marked 'rejected' are ignored by NavigationGraph
    and not traversed by Dijkstra pathfinding.
    """
    from ugm.navigation.graph import NavigationGraph
    storage = v2_test_env["storage"]
    game_id = "nav_rejection_game"

    storage.register_game(game_id, "Nav Rejection Game")
    # Transition 1: Valid path A -> B
    storage.record_transition(
        game_id=game_id,
        from_state="room_a",
        to_state="room_b",
        action_type="press_key",
        params={"key": "b"},
        success=True,
        cost=1.0,
        status="verified"
    )
    # Transition 2: Falsified shortcut A -> C (marked rejected)
    storage.record_transition(
        game_id=game_id,
        from_state="room_a",
        to_state="room_c",
        action_type="press_key",
        params={"key": "c"},
        success=False,
        cost=0.5,
        status="rejected"
    )

    # Initialize graph from storage
    graph = NavigationGraph(game_id=game_id, storage=storage)
    plan = graph.plan_route("room_a", "room_c")
    # Since A -> C is rejected, direct path should not be found
    assert plan.found is False

    # Path to B must still exist
    plan_b = graph.plan_route("room_a", "room_b")
    assert plan_b.found is True


def test_execution_error_does_not_penalize_graph_edge_cost(v2_test_env):
    """
    Verifies that transient execution errors (is_execution_error=True) do not penalize
    edge costs in NavigationGraph.
    """
    from ugm.navigation.graph import NavigationGraph
    from ugm.skills.contracts import NavigationEdge

    graph = NavigationGraph(game_id="edge_cost_game")
    edge = NavigationEdge(
        source_node_id="panel_1",
        target_node_id="panel_2",
        trigger_action="click",
        parameters={"x": 0.5, "y": 0.5},
        cost=1.0
    )
    graph.register_edge(edge, persist=False)

    # Record execution error (e.g. transient focus glitch)
    graph.record_transition_result(
        from_state="panel_1",
        to_state="panel_2",
        action_type="click",
        params={"x": 0.5, "y": 0.5},
        success=False,
        is_execution_error=True
    )
    # Cost should remain unchanged at 1.0
    edges = graph.adj_list["panel_1"]
    assert edges[0].cost == 1.0

    # True postcondition failure (is_execution_error=False) must penalize cost
    graph.record_transition_result(
        from_state="panel_1",
        to_state="panel_2",
        action_type="click",
        params={"x": 0.5, "y": 0.5},
        success=False,
        is_execution_error=False
    )
    assert edges[0].cost > 1.0


@pytest.mark.asyncio
async def test_mcp_tools_default_to_active_game_id(v2_test_env):
    """
    Verifies that after discover_and_attach_game attaches to an unknown game,
    subsequent MCP calls (get_ui_state, memory_search, checkpointing) default
    to the active attached game without requiring manual game_id passing.
    """
    server = v2_test_env["server"]
    ctx = v2_test_env["ctx"]

    # Attach to stellaris
    attach_res = await server.call_tool("discover_and_attach_game", {
        "window_title": "Stellaris",
        "process_name": "stellaris.exe"
    })
    attach_data = extract_data(attach_res)
    assert attach_data["game_id"] == "stellaris"
    assert ctx.active_game_id == "stellaris"

    # 1. get_ui_state without game_id
    ui_res = await server.call_tool("get_ui_state", {})
    ui_data = extract_data(ui_res)
    assert "state_name" in ui_data

    # 2. learn_skill without game_id
    learn_res = await server.call_tool("learn_skill", {
        "skill_id": "galaxy_map_toggle",
        "name": "Galaxy Map Toggle",
        "steps": [{"action": "press_key", "params": {"key": "m"}}]
    })
    learn_data = extract_data(learn_res)
    assert learn_data["success"] is True
    assert learn_data["game_id"] == "stellaris"

    # 3. memory_search without game_id
    search_res = await server.call_tool("memory_search", {
        "query": "galaxy",
        "search_type": "skills"
    })
    search_data = extract_data(search_res)
    assert search_data["game_id"] == "stellaris"
    assert len(search_data.get("learned_skills", [])) >= 1

    # 4. create_knowledge_checkpoint without game_id
    cp_res = await server.call_tool("create_knowledge_checkpoint", {
        "label": "initial_exploration"
    })
    cp_data = extract_data(cp_res)
    assert cp_data["success"] is True
    assert cp_data["game_id"] == "stellaris"

    # 5. list_knowledge_checkpoints without game_id
    list_cp_res = await server.call_tool("list_knowledge_checkpoints", {})
    list_cp_data = extract_data(list_cp_res)
    assert list_cp_data["game_id"] == "stellaris"
    assert list_cp_data["count"] >= 1


@pytest.mark.asyncio
async def test_optimize_skill_error_handling_via_mcp(v2_test_env):
    """
    Verifies that optimize_skill returns clean failure responses rather than unhandled
    crashes when presented with invalid primitives, empty steps, or curated skills.
    """
    server = v2_test_env["server"]
    storage = v2_test_env["storage"]
    game_id = "opt_test_game"
    skill_id = "test_opt_skill"

    storage.register_game(game_id, "Opt Test Game")
    storage.save_learned_skill(
        game_id=game_id,
        skill_id=skill_id,
        name="Test Opt Skill",
        steps=[{"action": "press_key", "params": {"key": "x"}}],
        initial_confidence=0.7
    )

    # 1. Invalid action primitive
    res_invalid = await server.call_tool("optimize_skill", {
        "skill_id": skill_id,
        "steps": [{"action": "unauthorized_arbitrary_eval", "params": {}}]
    })
    data_invalid = extract_data(res_invalid)
    assert data_invalid["success"] is False
    assert "rejected" in data_invalid["message"].lower()

    # 2. Empty steps
    res_empty = await server.call_tool("optimize_skill", {
        "skill_id": skill_id,
        "steps": []
    })
    data_empty = extract_data(res_empty)
    assert data_empty["success"] is False
    assert "rejected" in data_empty["message"].lower()

    # 3. Curated protected skill
    res_curated = await server.call_tool("optimize_skill", {
        "skill_id": "pause_unpause",
        "steps": [{"action": "press_key", "params": {"key": "p"}}]
    })
    data_curated = extract_data(res_curated)
    assert data_curated["success"] is False
    assert "protected" in data_curated["message"].lower() or "rejected" in data_curated["message"].lower()
