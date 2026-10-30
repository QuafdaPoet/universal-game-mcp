"""
Universal Game MCP (UGM) - Autonomous UI Navigator
Executes verified, step-by-step navigation across game menus and screens.
Validates state transitions after each action, checks emergency stops,
and replans or stops safely on failure without blindly proceeding.
"""

from __future__ import annotations
import time
import logging
from typing import Optional, List, Dict, Any, Tuple
from dataclasses import dataclass, asdict

from .recognizer import UIStateRecognizer, UIStateDetection
from .graph import NavigationGraph, PathResult
from ..skills.contracts import NavigationEdge
from ..core.safety import safety_manager, EmergencyStopTriggered, SafetyViolationError
from ..core.capture import CaptureManager
from ..core.input import InputController
from ..core.target import TargetExecutor, TargetResolver
from ..core.window import WindowManager

logger = logging.getLogger("ugm.navigation.navigator")


@dataclass
class NavigationStepLog:
    step_index: int
    from_state: str
    expected_to_state: str
    observed_to_state: str
    action_type: str
    params: Dict[str, Any]
    verified: bool
    latency_ms: float
    error: Optional[str] = None


@dataclass
class NavigationResult:
    success: bool
    start_state: str
    current_state: str
    target_state: str
    steps_executed: int
    steps_total: int
    step_logs: List[Dict[str, Any]]
    dry_run: bool
    message: str
    latency_ms: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class UINavigator:
    """
    Autonomous multi-step menu navigator.
    """

    def __init__(
        self,
        recognizer: UIStateRecognizer,
        graph: NavigationGraph,
        capture_manager: CaptureManager,
        input_controller: InputController,
        target_executor: TargetExecutor,
        target_resolver: TargetResolver
    ):
        self.recognizer = recognizer
        self.graph = graph
        self.capture_manager = capture_manager
        self.input_controller = input_controller
        self.target_executor = target_executor
        self.target_resolver = target_resolver

    def navigate_to(
        self,
        target_state: str,
        game_id: str = "hoi4",
        max_retries: int = 2,
        dry_run: bool = False
    ) -> NavigationResult:
        """
        Safely navigates from current game UI state to target_state.
        Validates post-state after every intermediate step.
        """
        t0 = time.perf_counter()
        effective_dry_run = self.input_controller.is_dry_run_active(dry_run)
        step_logs: List[NavigationStepLog] = []

        # Step 0: Emergency stop check
        try:
            safety_manager.check_safety()
        except EmergencyStopTriggered as e:
            return NavigationResult(
                success=False,
                start_state="unknown",
                current_state="unknown",
                target_state=target_state,
                steps_executed=0,
                steps_total=0,
                step_logs=[],
                dry_run=effective_dry_run,
                message=f"Navigation blocked: {e}",
                latency_ms=(time.perf_counter() - t0) * 1000.0
            )

        # Step 1: Observe current initial state
        win = self.input_controller.window_manager.current_window
        init_frame = self.capture_manager.capture(window=win)
        curr_det = self.recognizer.recognize_state(init_frame, game_id=game_id)
        start_state = curr_det.state_name
        current_state = start_state

        if current_state == target_state:
            return NavigationResult(
                success=True,
                start_state=start_state,
                current_state=current_state,
                target_state=target_state,
                steps_executed=0,
                steps_total=0,
                step_logs=[],
                dry_run=effective_dry_run,
                message=f"Already at target UI destination '{target_state}'.",
                latency_ms=(time.perf_counter() - t0) * 1000.0
            )

        # Step 2: Plan route from current state to target state
        path_res = self.graph.plan_route(current_state, target_state)
        if not path_res.found or not path_res.edges:
            return NavigationResult(
                success=False,
                start_state=start_state,
                current_state=current_state,
                target_state=target_state,
                steps_executed=0,
                steps_total=0,
                step_logs=[],
                dry_run=effective_dry_run,
                message=f"Navigation rejected: {path_res.message}",
                latency_ms=(time.perf_counter() - t0) * 1000.0
            )

        # Step 3: Traverse planned edges sequentially
        steps_total = len(path_res.edges)
        steps_executed = 0
        retries_remaining = max_retries

        for idx, edge in enumerate(path_res.edges):
            # Check safety killswitch before every action
            safety_manager.check_safety()

            step_t0 = time.perf_counter()
            expected_next = edge.target_node_id

            logger.info(
                "Navigating step [%d/%d]: '%s' -> '%s' via %s(%s)",
                idx + 1, steps_total, edge.source_node_id, expected_next,
                edge.trigger_action, edge.parameters
            )

            # Execute action
            exec_ok, exec_err = self._dispatch_navigation_action(edge, dry_run=effective_dry_run)
            if not exec_ok:
                step_log = NavigationStepLog(
                    step_index=idx + 1,
                    from_state=current_state,
                    expected_to_state=expected_next,
                    observed_to_state=current_state,
                    action_type=edge.trigger_action,
                    params=edge.parameters,
                    verified=False,
                    latency_ms=(time.perf_counter() - step_t0) * 1000.0,
                    error=f"Dispatch failed: {exec_err}"
                )
                step_logs.append(step_log)
                self.graph.record_transition_result(
                    from_state=current_state,
                    to_state=expected_next,
                    action_type=edge.trigger_action,
                    params=edge.parameters,
                    success=False,
                    source="live" if not effective_dry_run else "synthetic_fixture",
                    is_execution_error=True
                )
                return NavigationResult(
                    success=False,
                    start_state=start_state,
                    current_state=current_state,
                    target_state=target_state,
                    steps_executed=steps_executed,
                    steps_total=steps_total,
                    step_logs=[asdict(s) for s in step_logs],
                    dry_run=effective_dry_run,
                    message=f"Navigation aborted at step {idx + 1}: {exec_err}",
                    latency_ms=(time.perf_counter() - t0) * 1000.0
                )

            # Observe resulting state
            time.sleep(0.08)
            post_frame = self.capture_manager.capture(window=win)

            if effective_dry_run:
                # In dry-run simulation mode, simulate transition
                observed_state = expected_next
                step_verified = True
            else:
                post_det = self.recognizer.recognize_state(post_frame, game_id=game_id)
                observed_state = post_det.state_name
                step_verified = (observed_state == expected_next or (idx == steps_total - 1 and observed_state == target_state))

            # Record step log and transition feedback
            step_log = NavigationStepLog(
                step_index=idx + 1,
                from_state=current_state,
                expected_to_state=expected_next,
                observed_to_state=observed_state,
                action_type=edge.trigger_action,
                params=edge.parameters,
                verified=step_verified,
                latency_ms=(time.perf_counter() - step_t0) * 1000.0,
                error=None if step_verified else f"Expected state '{expected_next}', but observed '{observed_state}'"
            )
            step_logs.append(step_log)
            steps_executed += 1

            self.graph.record_transition_result(
                from_state=current_state,
                to_state=expected_next,
                action_type=edge.trigger_action,
                params=edge.parameters,
                success=step_verified,
                source="live" if not effective_dry_run else "synthetic_fixture",
                is_execution_error=False
            )

            if not step_verified:
                if retries_remaining > 0:
                    logger.warning("Navigation step %d transition verification failed. Attempting replan...", idx + 1)
                    retries_remaining -= 1
                    # Attempt replanning from the currently observed state
                    replan = self.graph.plan_route(observed_state, target_state)
                    if replan.found and replan.edges:
                        logger.info("Replanned alternate route with %d remaining steps.", len(replan.edges))
                        # Replace rest of queue with replanned route
                        path_res.edges = replan.edges
                        steps_total = steps_executed + len(replan.edges)
                        current_state = observed_state
                        continue

                # Stop safely if cannot recover
                return NavigationResult(
                    success=False,
                    start_state=start_state,
                    current_state=observed_state,
                    target_state=target_state,
                    steps_executed=steps_executed,
                    steps_total=steps_total,
                    step_logs=[asdict(s) for s in step_logs],
                    dry_run=effective_dry_run,
                    message=(
                        f"Navigation failed at step {idx + 1}: Expected state '{expected_next}', "
                        f"but observed '{observed_state}'. Safely halted without blind inputs."
                    ),
                    latency_ms=(time.perf_counter() - t0) * 1000.0
                )

            current_state = observed_state

        all_success = (current_state == target_state)
        return NavigationResult(
            success=all_success,
            start_state=start_state,
            current_state=current_state,
            target_state=target_state,
            steps_executed=steps_executed,
            steps_total=steps_total,
            step_logs=[asdict(s) for s in step_logs],
            dry_run=effective_dry_run,
            message=f"Navigation {'succeeded' if all_success else 'halted'} (current: {current_state}).",
            latency_ms=(time.perf_counter() - t0) * 1000.0
        )

    def _dispatch_navigation_action(
        self,
        edge: NavigationEdge,
        dry_run: bool = False
    ) -> Tuple[bool, Optional[str]]:
        """Executes a single navigation action primitive safely."""
        action = edge.trigger_action
        params = edge.parameters

        if action == "press_keys":
            keys = params.get("keys", [])
            dur = params.get("duration_ms", 100)
            res = self.input_controller.press_keys(keys=keys, duration_ms=dur, dry_run=dry_run)
            return res.success, res.message if not res.success else None

        elif action in ("click_selector", "locate_and_click"):
            sel_id = params.get("selector_id") or params.get("selector")
            btn = params.get("button", "left")
            clicks = params.get("clicks", 1)
            res = self.target_executor.click_and_verify(
                target_resolution=None,
                button=btn,
                clicks=clicks,
                dry_run=dry_run
            )
            return res.executed or res.dry_run, res.reason if not (res.executed or res.dry_run) else None

        elif action == "click_target":
            nx = params.get("x", 0.5)
            ny = params.get("y", 0.5)
            btn = params.get("button", "left")
            res = self.input_controller.click(norm_x=nx, norm_y=ny, button=btn, dry_run=dry_run)
            return res.executed or res.dry_run, res.reason if not (res.executed or res.dry_run) else None

        elif action == "wait":
            dur_ms = params.get("duration_ms", 500)
            safety_manager.check_safety()
            time.sleep(dur_ms / 1000.0)
            return True, None

        else:
            return False, f"Unsupported navigation action primitive: '{action}'"
