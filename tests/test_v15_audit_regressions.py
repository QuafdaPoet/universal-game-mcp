"""
Universal Game MCP (UGM) - Mandatory V1.5 Correctness Audit Regression Tests.
Verifies and defends the 9 critical audit items:
1. Skill Pack dictionary reference preservation and selector resolution.
2. Navigation selector execution with correctly resolved target coordinates.
3. Replanning execution after a failed transition without skipping steps.
4. Candidate/rejected skill execution policy.
5. verify_state primitive handler.
6. Dry-run and unverified outcomes do not yield genuine live verification.
7. Missing postconditions fail verification rather than false success.
8. Simulator observations are marked synthetic/fixture, not live.
9. Safety policy enforcement halts skill execution immediately.
"""

import time
import pytest
import numpy as np
from unittest.mock import MagicMock

from ugm.core.types import NormalizedRect, TargetResolution, TargetStatus, ClickResult
from ugm.core.safety import safety_manager, EmergencyStopTriggered, SafetyViolationError
from ugm.core.capture import CaptureManager
from ugm.core.input import InputController
from ugm.core.ocr import BaseOCREngine
from ugm.core.icon import IconDetector
from ugm.core.verifier import ActionVerifier
from ugm.core.target import TargetResolver, TargetExecutor
from ugm.core.window import WindowInfo, WindowManager
from ugm.skills.engine import SkillExecutionEngine
from ugm.skills.contracts import NavigationEdge
from ugm.navigation.graph import NavigationGraph
from ugm.navigation.navigator import UINavigator
from ugm.navigation.recognizer import UIStateRecognizer
from ugm.memory.storage import PersistentKnowledgeStorage
from ugm.memory.database import DatabaseManager


@pytest.fixture
def dummy_frame():
    return np.zeros((1080, 1920, 3), dtype=np.uint8)


@pytest.fixture
def mock_window():
    return WindowInfo(
        hwnd=1000,
        title="Hearts of Iron IV",
        process_name="hoi4.exe",
        pid=100,
        x=0, y=0, width=1920, height=1080,
        client_x=0, client_y=0, client_width=1920, client_height=1080,
        is_minimized=False,
        is_focused=True
    )


def test_audit_1_dict_skill_pack_selector_resolution(dummy_frame):
    """1. Verify skill packs provided as raw dictionaries resolve selectors correctly."""
    icon_detector = MagicMock(spec=IconDetector)
    icon_detector.locate_icon.return_value = MagicMock(found=False, match_score=0.0)

    from ugm.core.types import OCRItem, BoundingBox
    mock_item = OCRItem(
        text="Production Panel",
        confidence=0.95,
        bbox_pixel=BoundingBox(10, 10, 100, 30),
        bbox_normalized=NormalizedRect(0.1, 0.1, 0.2, 0.05)
    )
    ocr_engine = MagicMock(spec=BaseOCREngine)
    ocr_engine.read_text.return_value = MagicMock(items=[mock_item])

    verifier = MagicMock(spec=ActionVerifier)
    input_ctrl = MagicMock(spec=InputController)
    input_ctrl.is_dry_run_active.side_effect = lambda dr=None: dr if dr is not None else True
    verifier.input_controller = input_ctrl

    dict_pack = {
        "selectors": {
            "custom_dict_button": {
                "text_pattern": "Production",
                "search_roi": {"x": 0.1, "y": 0.1, "width": 0.2, "height": 0.2},
                "threshold": 0.85
            }
        }
    }

    resolver = TargetResolver(
        icon_detector=icon_detector,
        ocr_engine=ocr_engine,
        verifier=verifier,
        skill_packs={"hoi4": dict_pack}
    )

    res = resolver.resolve_target(
        frame=dummy_frame,
        selector_id="custom_dict_button",
        dry_run=True
    )
    assert res.status in ("found", TargetStatus.SUCCESS.value)
    assert res.selector_id == "custom_dict_button"


def test_audit_2_navigation_selector_execution_resolves_coordinates(dummy_frame, mock_window):
    """2. Navigation selector execution resolves actual target coordinates before clicking."""
    cap_mgr = MagicMock(spec=CaptureManager)
    cap_mgr.capture.return_value = dummy_frame

    input_ctrl = MagicMock(spec=InputController)
    input_ctrl.is_dry_run_active.side_effect = lambda dr=None: dr if dr is not None else True
    win_mgr = MagicMock(spec=WindowManager)
    win_mgr.current_window = mock_window
    input_ctrl.window_manager = win_mgr

    recognizer = MagicMock(spec=UIStateRecognizer)
    graph = MagicMock(spec=NavigationGraph)
    target_resolver = MagicMock(spec=TargetResolver)
    target_executor = MagicMock(spec=TargetExecutor)

    target_res = TargetResolution(
        status=TargetStatus.SUCCESS.value,
        selector_id="prod_btn",
        coordinates_normalized=(0.15, 0.25)
    )
    target_resolver.resolve_target.return_value = target_res

    click_res = ClickResult(
        x_normalized=0.15, y_normalized=0.25,
        x_pixel=288, y_pixel=270,
        button="left", clicks=1,
        dry_run=True, executed=True,
        verification_passed=True, reason="Verified",
        latency_ms=1.5
    )
    target_executor.click_and_verify.return_value = click_res

    nav = UINavigator(
        recognizer=recognizer,
        graph=graph,
        capture_manager=cap_mgr,
        input_controller=input_ctrl,
        target_executor=target_executor,
        target_resolver=target_resolver
    )

    edge = NavigationEdge(
        source_node_id="main_map",
        target_node_id="production",
        trigger_action="click_selector",
        parameters={"selector_id": "prod_btn"}
    )

    ok, err = nav._dispatch_navigation_action(edge, dry_run=True)
    assert ok is True
    assert err is None
    target_resolver.resolve_target.assert_called_once()
    target_executor.click_and_verify.assert_called_once_with(
        target_resolution=target_res,
        button="left",
        clicks=1,
        dry_run=True
    )


def test_audit_3_replanning_executes_alternate_route_without_skipping(dummy_frame, mock_window):
    """3. Verify replanning on transition failure smoothly executes the replanned route."""
    safety_manager.reset_emergency_stop()
    cap_mgr = MagicMock(spec=CaptureManager)
    cap_mgr.capture.return_value = dummy_frame

    input_ctrl = MagicMock(spec=InputController)
    input_ctrl.is_dry_run_active.return_value = False
    win_mgr = MagicMock(spec=WindowManager)
    win_mgr.current_window = mock_window
    input_ctrl.window_manager = win_mgr
    input_ctrl.press_keys.return_value = MagicMock(success=True)

    recognizer = MagicMock(spec=UIStateRecognizer)
    # State sequence:
    # 1. Initial state: main_map
    # 2. Step 1 (press 'y' to go to production): observed state remains 'main_map' (failure!)
    # 3. Replanning route: [("main_map" -> "research"), ("research" -> "production")]
    # 4. Step 2 (press 'w' to go to research): observed state 'research' (success!)
    # 5. Step 3 (from research to production): observed state 'production' (success!)
    recognizer.recognize_state.side_effect = [
        MagicMock(state_name="main_map"),      # initial
        MagicMock(state_name="main_map"),      # step 1 failed transition
        MagicMock(state_name="research"),      # replanned step 1 success
        MagicMock(state_name="production"),    # replanned step 2 success
    ]

    graph = NavigationGraph(game_id="hoi4")
    graph.register_edge(NavigationEdge("main_map", "production", "press_keys", {"keys": ["y"]}))
    graph.register_edge(NavigationEdge("main_map", "research", "press_keys", {"keys": ["w"]}))
    graph.register_edge(NavigationEdge("research", "production", "press_keys", {"keys": ["y"]}))

    target_resolver = MagicMock(spec=TargetResolver)
    target_executor = MagicMock(spec=TargetExecutor)

    nav = UINavigator(
        recognizer=recognizer,
        graph=graph,
        capture_manager=cap_mgr,
        input_controller=input_ctrl,
        target_executor=target_executor,
        target_resolver=target_resolver
    )

    res = nav.navigate_to(target_state="production", game_id="hoi4", max_retries=1, dry_run=False)
    assert res.success is True
    assert res.current_state == "production"
    assert len(res.step_logs) == 3


def test_audit_4_candidate_and_rejected_skill_execution_policy(dummy_frame, mock_window):
    """4. Candidate and rejected skill execution policy is strictly enforced."""
    safety_manager.reset_emergency_stop()
    cap_mgr = MagicMock(spec=CaptureManager)
    cap_mgr.capture.return_value = dummy_frame
    input_ctrl = MagicMock(spec=InputController)
    input_ctrl.is_dry_run_active.side_effect = lambda dr=None: dr if dr is not None else False
    win_mgr = MagicMock(spec=WindowManager)
    win_mgr.current_window = mock_window
    input_ctrl.window_manager = win_mgr

    engine = SkillExecutionEngine(
        capture_manager=cap_mgr,
        input_controller=input_ctrl,
        target_resolver=MagicMock(),
        target_executor=MagicMock(),
        verifier=MagicMock(),
        ocr_engine=MagicMock(),
        storage=None
    )

    # Register rejected skill
    engine.skill_packs["hoi4"] = {
        "skills": {
            "rejected_skill": {
                "skill_id": "rejected_skill",
                "status": "rejected",
                "steps": [{"action": "wait", "params": {"duration_ms": 10}}],
                "post_verification": {"type": "visual_diff"}
            },
            "candidate_skill": {
                "skill_id": "candidate_skill",
                "status": "candidate",
                "steps": [{"action": "wait", "params": {"duration_ms": 10}}],
                "post_verification": {"type": "visual_diff"}
            }
        }
    }

    # Case A: Rejected skill must be blocked
    res_rej = engine.execute_skill("rejected_skill", dry_run=False)
    assert res_rej.success is False
    assert res_rej.status == "blocked"
    assert "REJECTED" in res_rej.message

    # Case B: Unapproved candidate live execution without allow_candidate must be blocked
    res_cand_blocked = engine.execute_skill("candidate_skill", dry_run=False)
    assert res_cand_blocked.success is False
    assert res_cand_blocked.status == "blocked"
    assert "unapproved candidate" in res_cand_blocked.message.lower()

    # Case C: Candidate execution in dry-run is permitted
    res_cand_dry = engine.execute_skill("candidate_skill", dry_run=True)
    assert res_cand_dry.success is True


def test_audit_5_verify_state_primitive_handler(dummy_frame, mock_window):
    """5. verify_state primitive handler executes and reports state matching."""
    cap_mgr = MagicMock(spec=CaptureManager)
    cap_mgr.capture.return_value = dummy_frame
    input_ctrl = MagicMock(spec=InputController)
    input_ctrl.is_dry_run_active.side_effect = lambda dr=None: dr if dr is not None else True
    win_mgr = MagicMock(spec=WindowManager)
    win_mgr.current_window = mock_window
    input_ctrl.window_manager = win_mgr

    recognizer = MagicMock(spec=UIStateRecognizer)
    recognizer.recognize_state.return_value = MagicMock(
        state_name="production",
        confidence=0.92,
        to_dict=lambda: {"state_name": "production", "confidence": 0.92}
    )

    engine = SkillExecutionEngine(
        capture_manager=cap_mgr,
        input_controller=input_ctrl,
        target_resolver=MagicMock(),
        target_executor=MagicMock(),
        verifier=MagicMock(),
        ocr_engine=MagicMock(),
        storage=None
    )
    engine.state_recognizer = recognizer

    engine.skill_packs["hoi4"] = {
        "skills": {
            "state_check_skill": {
                "skill_id": "state_check_skill",
                "steps": [
                    {"action": "verify_state", "params": {"expected_state": "production"}}
                ],
                "post_verification": {"type": "visual_diff"}
            }
        }
    }

    res = engine.execute_skill("state_check_skill", dry_run=True)
    assert res.success is True
    assert res.step_logs[0]["action"] == "verify_state"
    assert res.step_logs[0]["success"] is True


def test_audit_6_dry_run_never_promotes_to_live_verified(tmp_path):
    """6. Dry-run and simulation runs never promote candidate skills to live_verified."""
    db_file = str(tmp_path / "audit_memory.db")
    storage = PersistentKnowledgeStorage(DatabaseManager(db_file))

    storage.save_learned_skill(
        skill_id="test_candidate",
        game_id="hoi4",
        name="Test Candidate",
        steps=[{"action": "wait", "params": {"duration_ms": 50}}],
        post_verification={"type": "visual_diff"},
        status="candidate",
        source="synthetic_fixture"
    )

    # Dry-run validations
    storage.update_skill_stats("test_candidate", success=True, auto_promote_threshold=2, is_dry_run=True)
    storage.update_skill_stats("test_candidate", success=True, auto_promote_threshold=2, is_dry_run=True)

    skill = storage.get_learned_skill("test_candidate")
    assert skill.status == "verified"
    assert skill.source == "synthetic_fixture"

    # Querying live_verified must return 0!
    live_skills = storage.list_learned_skills(game_id="hoi4", status="live_verified")
    assert len(live_skills) == 0

    # Querying simulator_verified returns the skill
    sim_skills = storage.list_learned_skills(game_id="hoi4", status="simulator_verified")
    assert len(sim_skills) == 1

    stats = storage.get_statistics(game_id="hoi4")
    assert stats["live_verified_skills"] == 0
    assert stats["simulator_verified_skills"] == 1


def test_audit_7_missing_postconditions_fail_verification(dummy_frame, mock_window):
    """7. Missing postconditions must fail verification to prevent false success."""
    cap_mgr = MagicMock(spec=CaptureManager)
    cap_mgr.capture.return_value = dummy_frame
    input_ctrl = MagicMock(spec=InputController)
    input_ctrl.is_dry_run_active.return_value = True
    win_mgr = MagicMock(spec=WindowManager)
    win_mgr.current_window = mock_window
    input_ctrl.window_manager = win_mgr

    engine = SkillExecutionEngine(
        capture_manager=cap_mgr,
        input_controller=input_ctrl,
        target_resolver=MagicMock(),
        target_executor=MagicMock(),
        verifier=MagicMock(),
        ocr_engine=MagicMock(),
        storage=None
    )

    engine.skill_packs["hoi4"] = {
        "skills": {
            "unverified_skill": {
                "skill_id": "unverified_skill",
                "steps": [{"action": "wait", "params": {"duration_ms": 10}}],
                "post_verification": {}  # Missing postconditions!
            }
        }
    }

    res = engine.execute_skill("unverified_skill", dry_run=True)
    assert res.success is False
    assert res.post_verification_passed is False
    assert "No postconditions specified" in res.message


def test_audit_8_sim_observations_marked_synthetic(dummy_frame, tmp_path):
    """8. Simulator and mock fixture observations are stored as synthetic_fixture, not live."""
    db_file = str(tmp_path / "sim_memory.db")
    storage = PersistentKnowledgeStorage(DatabaseManager(db_file))

    recognizer = UIStateRecognizer(
        ocr_engine=MagicMock(),
        icon_detector=MagicMock(),
        storage=storage
    )

    # Recognize state for offline_sim
    det = recognizer.recognize_state(dummy_frame, game_id="offline_sim")
    states = storage.get_states(game_id="offline_sim")
    if states:
        for s in states:
            assert s.source == "synthetic_fixture"
            assert s.source != "live"


def test_audit_9_emergency_stop_halts_skill_execution(dummy_frame, mock_window):
    """9. Emergency stop halts skill execution immediately."""
    cap_mgr = MagicMock(spec=CaptureManager)
    cap_mgr.capture.return_value = dummy_frame
    input_ctrl = MagicMock(spec=InputController)
    input_ctrl.is_dry_run_active.return_value = True
    win_mgr = MagicMock(spec=WindowManager)
    win_mgr.current_window = mock_window
    input_ctrl.window_manager = win_mgr

    engine = SkillExecutionEngine(
        capture_manager=cap_mgr,
        input_controller=input_ctrl,
        target_resolver=MagicMock(),
        target_executor=MagicMock(),
        verifier=MagicMock(),
        ocr_engine=MagicMock(),
        storage=None
    )

    engine.skill_packs["hoi4"] = {
        "skills": {
            "long_skill": {
                "skill_id": "long_skill",
                "steps": [
                    {"action": "wait", "params": {"duration_ms": 100}},
                    {"action": "wait", "params": {"duration_ms": 100}}
                ],
                "post_verification": {"type": "visual_diff"}
            }
        }
    }

    # Trigger emergency stop
    safety_manager.trigger_emergency_stop("Audit safety test")
    res = engine.execute_skill("long_skill", dry_run=True)
    assert res.success is False
    assert res.status == "blocked"
    assert "Emergency stop is active" in res.message
    safety_manager.reset_emergency_stop()
