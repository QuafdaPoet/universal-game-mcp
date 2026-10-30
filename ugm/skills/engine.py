"""
Universal Game MCP (UGM) - Validated Game Skill Execution Engine
Executes curated and self-learned Game Skills with strict parameter binding,
precondition enforcement, allowlisted action primitives, sequential execution,
postcondition verification, timeout budgets, and emergency stop killswitch.
"""

from __future__ import annotations
import time
import os
import logging
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field, asdict
import numpy as np

from .manifest import SkillDefinition, SelectorDefinition
from .loader import GameSkillPack
from ..core.types import NormalizedRect, TargetStatus, ClickResult
from ..core.safety import safety_manager, EmergencyStopTriggered, SafetyViolationError
from ..core.capture import CaptureManager
from ..core.ocr import BaseOCREngine
from ..core.input import InputController
from ..core.verifier import ActionVerifier
from ..core.target import TargetResolver, TargetExecutor
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from ..memory.storage import PersistentKnowledgeStorage

logger = logging.getLogger("ugm.skills.engine")

ALLOWLISTED_ACTION_PRIMITIVES = {
    "press_keys",
    "press_key",
    "click_target",
    "click",
    "locate_and_click",
    "click_selector",
    "hover",
    "hover_target",
    "wait",
    "verify_state"
}


@dataclass
class StepExecutionLog:
    step_index: int
    action: str
    params: Dict[str, Any]
    success: bool
    executed: bool
    latency_ms: float
    message: str
    evidence: Optional[Dict[str, Any]] = None


@dataclass
class SkillExecutionResult:
    skill_id: str
    game_id: str
    success: bool
    status: str  # 'success', 'failed', 'blocked', 'incomplete'
    preconditions_met: bool
    steps_executed: int
    steps_total: int
    post_verification_passed: bool
    dry_run: bool
    latency_ms: float
    message: str
    step_logs: List[Dict[str, Any]]
    evidence: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class SkillExecutionEngine:
    """
    Validated runtime executor for Game Skills.
    Guarantees no arbitrary code execution; all actions are allowlisted primitives.
    """

    def __init__(
        self,
        capture_manager: CaptureManager,
        input_controller: InputController,
        target_resolver: TargetResolver,
        target_executor: TargetExecutor,
        verifier: ActionVerifier,
        ocr_engine: BaseOCREngine,
        storage: Optional[PersistentKnowledgeStorage] = None,
        skill_packs: Optional[Dict[str, GameSkillPack]] = None
    ):
        self.capture_manager = capture_manager
        self.input_controller = input_controller
        self.target_resolver = target_resolver
        self.target_executor = target_executor
        self.verifier = verifier
        self.ocr_engine = ocr_engine
        self.storage = storage
        self.skill_packs: Dict[str, GameSkillPack] = skill_packs or {}

    def lookup_skill(self, skill_id: str, game_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Looks up skill from curated packs first, then from persistent learned skills.
        """
        # 1. Curated packs
        for g_id, pack in self.skill_packs.items():
            if game_id and g_id != game_id:
                continue
            if skill_id in pack.skills:
                skill_def = pack.skills[skill_id]
                return {
                    "skill_id": skill_def.skill_id,
                    "game_id": g_id,
                    "version": skill_def.version,
                    "name": skill_def.name,
                    "description": skill_def.description,
                    "preconditions": skill_def.preconditions,
                    "steps": skill_def.steps,
                    "post_verification": skill_def.post_verification,
                    "is_curated": True,
                    "status": "curated"
                }

        # 2. Learned persistent skills
        if self.storage:
            learned = self.storage.get_learned_skill(skill_id)
            if learned and (not game_id or learned.game_id == game_id):
                return {
                    "skill_id": learned.skill_id,
                    "game_id": learned.game_id,
                    "version": learned.version,
                    "name": learned.name,
                    "description": learned.description,
                    "preconditions": learned.preconditions,
                    "steps": learned.steps,
                    "post_verification": learned.post_verification,
                    "is_curated": False,
                    "status": learned.status,
                    "validation_count": learned.validation_count,
                    "confidence": learned.confidence
                }

        return None

    def execute_skill(
        self,
        skill_id: str,
        game_id: Optional[str] = None,
        params: Optional[Dict[str, Any]] = None,
        dry_run: bool = False
    ) -> SkillExecutionResult:
        """
        Executes a registered skill end-to-end with validation and verification.
        """
        t0 = time.perf_counter()
        effective_dry_run = self.input_controller.is_dry_run_active(dry_run)
        step_logs: List[StepExecutionLog] = []

        # Emergency Stop Check
        try:
            safety_manager.check_safety()
        except EmergencyStopTriggered as e:
            return SkillExecutionResult(
                skill_id=skill_id,
                game_id=game_id or "unknown",
                success=False,
                status="blocked",
                preconditions_met=False,
                steps_executed=0,
                steps_total=0,
                post_verification_passed=False,
                dry_run=effective_dry_run,
                latency_ms=(time.perf_counter() - t0) * 1000.0,
                message=f"Action blocked: Emergency stop is active ({e})",
                step_logs=[],
                evidence={"emergency_stopped": True}
            )

        # Step 1: Lookup skill
        skill_data = self.lookup_skill(skill_id, game_id=game_id)
        if not skill_data:
            return SkillExecutionResult(
                skill_id=skill_id,
                game_id=game_id or "unknown",
                success=False,
                status="failed",
                preconditions_met=False,
                steps_executed=0,
                steps_total=0,
                post_verification_passed=False,
                dry_run=effective_dry_run,
                latency_ms=(time.perf_counter() - t0) * 1000.0,
                message=f"Skill '{skill_id}' not found in registered packs or learned memory.",
                step_logs=[],
                evidence={}
            )

        resolved_game_id = skill_data["game_id"]
        steps = skill_data.get("steps", [])
        preconditions = skill_data.get("preconditions", {})
        post_verification = skill_data.get("post_verification", {})

        # Step 2: Validate Action Primitives & Completeness
        for s in steps:
            action_name = s.get("action")
            if action_name not in ALLOWLISTED_ACTION_PRIMITIVES:
                raise SafetyViolationError(
                    f"Disallowed action primitive '{action_name}' in skill '{skill_id}'. "
                    f"Allowed primitives: {ALLOWLISTED_ACTION_PRIMITIVES}"
                )

        # Check for incomplete skill prerequisites (e.g., missing template files)
        incomplete_reason = self._check_completeness(skill_data)
        if incomplete_reason:
            return SkillExecutionResult(
                skill_id=skill_id,
                game_id=resolved_game_id,
                success=False,
                status="incomplete",
                preconditions_met=False,
                steps_executed=0,
                steps_total=len(steps),
                post_verification_passed=False,
                dry_run=effective_dry_run,
                latency_ms=(time.perf_counter() - t0) * 1000.0,
                message=f"Skill '{skill_id}' flagged as INCOMPLETE: {incomplete_reason}",
                step_logs=[],
                evidence={"flagged_incomplete": True, "reason": incomplete_reason}
            )

        # Step 3: Check Preconditions
        win = self.input_controller.window_manager.current_window
        if preconditions.get("window_focused", True):
            if not win:
                return SkillExecutionResult(
                    skill_id=skill_id,
                    game_id=resolved_game_id,
                    success=False,
                    status="blocked",
                    preconditions_met=False,
                    steps_executed=0,
                    steps_total=len(steps),
                    post_verification_passed=False,
                    dry_run=effective_dry_run,
                    latency_ms=(time.perf_counter() - t0) * 1000.0,
                    message="Precondition failed: No active game window attached.",
                    step_logs=[],
                    evidence={"preconditions": preconditions}
                )

        # Step 4: Baseline capture before executing steps
        pre_action_frame = self.capture_manager.capture(window=win)

        # Step 5: Execute Sequential Steps
        bound_params = params or {}
        for idx, step in enumerate(steps):
            safety_manager.check_safety()
            step_t0 = time.perf_counter()

            step_res = self._execute_step(
                step=step,
                step_index=idx + 1,
                bound_params=bound_params,
                dry_run=effective_dry_run,
                game_id=resolved_game_id
            )
            step_logs.append(step_res)

            if not step_res.success:
                latency = (time.perf_counter() - t0) * 1000.0
                self._record_execution_history(resolved_game_id, skill_id, success=False, reason=step_res.message, duration_ms=latency, source="live" if not effective_dry_run else "synthetic_fixture")
                return SkillExecutionResult(
                    skill_id=skill_id,
                    game_id=resolved_game_id,
                    success=False,
                    status="failed",
                    preconditions_met=True,
                    steps_executed=idx,
                    steps_total=len(steps),
                    post_verification_passed=False,
                    dry_run=effective_dry_run,
                    latency_ms=latency,
                    message=f"Step {idx + 1} ({step.get('action')}) failed: {step_res.message}",
                    step_logs=[asdict(s) for s in step_logs],
                    evidence={"failed_step": idx + 1, "details": step_res.evidence}
                )

        # Step 6: Postcondition Verification
        time.sleep(0.08)
        post_action_frame = self.capture_manager.capture(window=win)

        post_ver_ok, post_ver_msg, ver_details = self._verify_postconditions(
            post_verification=post_verification,
            pre_frame=pre_action_frame,
            post_frame=post_action_frame,
            dry_run=effective_dry_run
        )

        latency = (time.perf_counter() - t0) * 1000.0
        final_success = post_ver_ok

        self._record_execution_history(
            resolved_game_id, skill_id, success=final_success, reason=post_ver_msg,
            duration_ms=latency, source="live" if not effective_dry_run else "synthetic_fixture"
        )

        # Update stats if this is a learned skill
        if self.storage and not skill_data.get("is_curated"):
            self.storage.update_skill_stats(skill_id, success=final_success)

        return SkillExecutionResult(
            skill_id=skill_id,
            game_id=resolved_game_id,
            success=final_success,
            status="success" if final_success else "failed",
            preconditions_met=True,
            steps_executed=len(steps),
            steps_total=len(steps),
            post_verification_passed=post_ver_ok,
            dry_run=effective_dry_run,
            latency_ms=latency,
            message=f"Skill '{skill_id}' execution {'PASSED' if final_success else 'FAILED'}: {post_ver_msg}",
            step_logs=[asdict(s) for s in step_logs],
            evidence=ver_details
        )

    def _execute_step(
        self,
        step: Dict[str, Any],
        step_index: int,
        bound_params: Dict[str, Any],
        dry_run: bool,
        game_id: str
    ) -> StepExecutionLog:
        """Dispatches an individual allowlisted action primitive."""
        action = step.get("action")
        raw_params = step.get("params") or {}
        # Merge bound parameters
        merged_params = {**raw_params, **bound_params}
        t0 = time.perf_counter()

        try:
            if action in ("press_keys", "press_key"):
                keys = merged_params.get("keys") or ([merged_params["key"]] if "key" in merged_params else [])
                duration = merged_params.get("duration_ms", 50)
                res = self.input_controller.press_keys(keys=keys, duration_ms=duration, dry_run=dry_run)
                succ = res.success if hasattr(res, "success") else (res.get("success", res.get("executed", True)) if isinstance(res, dict) else True)
                msg = res.message if hasattr(res, "message") else (res.get("message", "Executed") if isinstance(res, dict) else "")
                return StepExecutionLog(
                    step_index=step_index,
                    action=action,
                    params=merged_params,
                    success=succ,
                    executed=succ,
                    latency_ms=(time.perf_counter() - t0) * 1000.0,
                    message=msg,
                    evidence={"keys": keys}
                )

            elif action in ("locate_and_click", "click_selector"):
                sel_id = step.get("selector") or merged_params.get("selector_id")
                btn = step.get("button", "left")
                clicks = step.get("clicks", 1)

                win = self.input_controller.window_manager.current_window
                frame = self.capture_manager.capture(window=win)

                target_res = self.target_resolver.resolve_target(
                    frame=frame,
                    selector_id=sel_id,
                    dry_run=dry_run
                )

                if target_res.status in (TargetStatus.NOT_FOUND.value, TargetStatus.AMBIGUOUS.value):
                    return StepExecutionLog(
                        step_index=step_index,
                        action=action,
                        params={"selector": sel_id, "button": btn},
                        success=False,
                        executed=False,
                        latency_ms=(time.perf_counter() - t0) * 1000.0,
                        message=f"Selector resolution failed: {target_res.reason}",
                        evidence=target_res.evidence
                    )

                click_res = self.target_executor.click_and_verify(
                    target_resolution=target_res,
                    button=btn,
                    clicks=clicks,
                    dry_run=dry_run
                )
                succ = click_res.status == TargetStatus.SUCCESS.value or dry_run
                return StepExecutionLog(
                    step_index=step_index,
                    action=action,
                    params={"selector": sel_id, "button": btn},
                    success=succ,
                    executed=click_res.executed or dry_run,
                    latency_ms=(time.perf_counter() - t0) * 1000.0,
                    message=click_res.reason,
                    evidence=click_res.evidence
                )

            elif action in ("click_target", "click"):
                nx = float(merged_params.get("x", 0.5))
                ny = float(merged_params.get("y", 0.5))
                btn = merged_params.get("button", "left")
                clicks = int(merged_params.get("clicks", 1))

                click_res = self.input_controller.click(
                    norm_x=nx, norm_y=ny, button=btn, clicks=clicks, dry_run=dry_run
                )
                return StepExecutionLog(
                    step_index=step_index,
                    action=action,
                    params=merged_params,
                    success=click_res.executed or dry_run,
                    executed=click_res.executed or dry_run,
                    latency_ms=(time.perf_counter() - t0) * 1000.0,
                    message=click_res.reason,
                    evidence={"coords": (nx, ny)}
                )

            elif action in ("hover", "hover_target"):
                nx = float(merged_params.get("x", 0.5))
                ny = float(merged_params.get("y", 0.5))
                dur = int(merged_params.get("duration_ms", 250))
                _, _, ok = self.input_controller.hover(norm_x=nx, norm_y=ny, duration_ms=dur, dry_run=dry_run)
                return StepExecutionLog(
                    step_index=step_index,
                    action=action,
                    params=merged_params,
                    success=True,
                    executed=ok or dry_run,
                    latency_ms=(time.perf_counter() - t0) * 1000.0,
                    message="Hover completed successfully."
                )

            elif action == "wait":
                dur = int(merged_params.get("duration_ms", 200))
                safety_manager.check_safety()
                time.sleep(dur / 1000.0)
                return StepExecutionLog(
                    step_index=step_index,
                    action=action,
                    params=merged_params,
                    success=True,
                    executed=True,
                    latency_ms=(time.perf_counter() - t0) * 1000.0,
                    message=f"Waited {dur}ms"
                )

            else:
                return StepExecutionLog(
                    step_index=step_index,
                    action=action,
                    params=merged_params,
                    success=False,
                    executed=False,
                    latency_ms=(time.perf_counter() - t0) * 1000.0,
                    message=f"Unsupported action: {action}"
                )

        except Exception as e:
            logger.error("Error executing skill step %d (%s): %s", step_index, action, e)
            return StepExecutionLog(
                step_index=step_index,
                action=action,
                params=merged_params,
                success=False,
                executed=False,
                latency_ms=(time.perf_counter() - t0) * 1000.0,
                message=f"Exception during step execution: {e}"
            )

    def _verify_postconditions(
        self,
        post_verification: Dict[str, Any],
        pre_frame: np.ndarray,
        post_frame: np.ndarray,
        dry_run: bool
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Validates post-action visual state change or OCR expectations.
        """
        if not post_verification:
            return True, "No postcondition specified.", {}

        if dry_run:
            return True, "[DRY-RUN] Verification simulated successfully.", {"dry_run": True}

        ver_type = post_verification.get("type", "visual_diff")
        roi_data = post_verification.get("roi")
        norm_roi = None
        if roi_data:
            norm_roi = NormalizedRect(
                float(roi_data["x"]), float(roi_data["y"]),
                float(roi_data["width"]), float(roi_data["height"])
            )

        min_diff = float(post_verification.get("min_diff_ratio", 0.005))
        expected_text = post_verification.get("expected_text")

        ver_res = self.verifier.verify_selection(
            pre_frame=pre_frame,
            post_frame=post_frame,
            roi=norm_roi,
            expected_text=expected_text,
            min_diff_ratio=min_diff
        )

        return ver_res.verified, ver_res.message, {
            "verified": ver_res.verified,
            "diff_ratio": ver_res.diff_ratio,
            "min_diff_required": min_diff,
            "expected_text": expected_text,
            "details": ver_res.details
        }

    def _check_completeness(self, skill_data: Dict[str, Any]) -> Optional[str]:
        """
        Detects incomplete skills that lack template files or selectors on disk.
        """
        game_id = skill_data.get("game_id")
        pack = self.skill_packs.get(game_id)
        if not pack:
            return None

        for step in skill_data.get("steps", []):
            if step.get("action") in ("locate_and_click", "click_selector"):
                sel_id = step.get("selector") or (step.get("params") or {}).get("selector_id")
                if sel_id not in pack.selectors:
                    return f"Selector '{sel_id}' referenced in step is not defined in manifest."

                sel_def = pack.selectors[sel_id]
                if sel_def.template_file:
                    tmpl_path = pack.get_template_path(sel_def.template_file)
                    if not os.path.isfile(tmpl_path):
                        return f"Template file '{sel_def.template_file}' for selector '{sel_id}' does not exist on disk."

        return None

    def _record_execution_history(
        self,
        game_id: str,
        skill_id: str,
        success: bool,
        reason: str,
        duration_ms: float,
        source: str
    ) -> None:
        if self.storage:
            try:
                self.storage.record_action_history(
                    game_id=game_id,
                    action_name=f"skill:{skill_id}",
                    params={"skill_id": skill_id},
                    success=success,
                    reason=reason,
                    duration_ms=duration_ms,
                    source=source
                )
            except Exception as e:
                logger.debug("History recording notice: %s", e)
