"""
Universal Game MCP (UGM) - Autonomous UI Explorer & Probing Engine
Provides generic UI affordance discovery, interactive element scanning,
and bounded Observe-Act-Verify probing for unsupported games.
"""

from __future__ import annotations
import time
import uuid
import logging
import math
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field, asdict
import numpy as np
import cv2

from .types import NormalizedRect, BoundingBox
from .safety import safety_manager, EmergencyStopTriggered, SafetyViolationError
from .capture import CaptureManager
from .window import game_id_for_window
from .input import InputController
from .verifier import ActionVerifier
from .ocr import BaseOCREngine
from .icon import IconDetector
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from ..memory.storage import PersistentKnowledgeStorage

logger = logging.getLogger("ugm.core.explorer")

PROBE_TIMEOUT_MIN_S = 0.05
PROBE_TIMEOUT_MAX_S = 10.0
_PROBE_POLL_S = 0.05
_PROBE_STABLE_S = 0.10
_PROBE_CHANGE_RATIO = 0.005
_PROBE_STABILITY_RATIO = 0.002
_PROBE_OCR_MIN_BUDGET_S = 0.05


class _ProbeTimeout(Exception):
    def __init__(self, phase: str):
        self.phase = phase


@dataclass
class _ProbeObservation:
    outcome: str
    diff_ratio: float = 0.0
    stable_frame: Optional[np.ndarray] = None
    transient_change: bool = False
    diff_measured: bool = False


@dataclass
class UIElementCandidate:
    candidate_id: str
    element_type: str  # 'button', 'text_anchor', 'icon', 'panel_tab', 'header'
    bbox: NormalizedRect
    center: Tuple[float, float]
    text: Optional[str] = None
    suggested_action: str = "click_target"
    confidence: float = 0.80
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        b = asdict(self.bbox)
        c = list(self.center)
        return {
            "candidate_id": self.candidate_id,
            "element_id": self.candidate_id,
            "element_type": self.element_type,
            "affordance_type": self.element_type,
            "bbox": b,
            "bbox_normalized": b,
            "center": c,
            "center_normalized": c,
            "text": self.text,
            "suggested_action": self.suggested_action,
            "confidence": round(self.confidence, 3),
            "details": self.details
        }


class UIExplorer:
    """
    Discovers UI elements and executes empirical Observe-Act-Verify probe actions
    without any manually authored per-game skills.
    """

    def __init__(
        self,
        capture_manager: CaptureManager,
        input_controller: InputController,
        verifier: ActionVerifier,
        ocr_engine: BaseOCREngine,
        icon_detector: IconDetector,
        storage: Optional[PersistentKnowledgeStorage] = None,
        max_probe_depth: int = 100
    ):
        self.capture_manager = capture_manager
        self.input_controller = input_controller
        self.verifier = verifier
        self.ocr_engine = ocr_engine
        self.icon_detector = icon_detector
        self.storage = storage
        self.max_probe_depth = max_probe_depth
        self.probe_count = 0
        self.active_game_id: Optional[str] = None

    def reset_probe_budget(self) -> None:
        """Resets the exploration probe step counter."""
        self.probe_count = 0

    def discover_interactive_elements(
        self,
        frame: np.ndarray,
        roi: Optional[NormalizedRect] = None,
        scan_mode: str = "all"
    ) -> List[UIElementCandidate]:
        """
        Scans a game frame (or ROI) for interactive candidates: text buttons,
        clickable tabs, visual anchors, and layout clusters.
        """
        candidates: List[UIElementCandidate] = []
        if frame is None or getattr(frame, "size", 1) == 0:
            return candidates

        h, w = frame.shape[:2]
        if h == 0 or w == 0:
            return candidates

        if roi is not None:
            if not (0 <= roi.x < roi.x + roi.width <= 1 and 0 <= roi.y < roi.y + roi.height <= 1):
                raise ValueError("Exploration ROI must fit inside the frame and have positive width and height.")
            bounds = roi.to_pixel(w, h)
            crop_x, crop_y, crop_w, crop_h = bounds.x, bounds.y, bounds.width, bounds.height
        else:
            crop_x, crop_y, crop_w, crop_h = 0, 0, w, h

        crop = frame[crop_y:crop_y + crop_h, crop_x:crop_x + crop_w]
        # Some OpenCV inputs make a bright, empty ROI look like one large
        # rectangle. No contrast means there is nothing to ground a proposal.
        if not crop.size or not np.any(crop != crop[0, 0]):
            return candidates

        # ----------------------------------------------------
        # 1. OCR-driven text element discovery
        # ----------------------------------------------------
        if scan_mode in ("all", "text", "buttons"):
            try:
                ocr_res = self.ocr_engine.read_text(frame, roi=roi, event_tag="explorer_scan")
                for idx, item in enumerate(ocr_res.items):
                    txt = item.text.strip()
                    if not txt:
                        continue

                    norm_bbox = item.bbox_normalized
                    center_x = round(norm_bbox.x + norm_bbox.width / 2.0, 4)
                    center_y = round(norm_bbox.y + norm_bbox.height / 2.0, 4)

                    elem_type = "button" if len(txt) <= 25 else "text_anchor"
                    action = "click_target" if elem_type == "button" else "hover"

                    candidates.append(UIElementCandidate(
                        candidate_id=f"elem_text_{idx}_{txt[:8].lower().replace(' ', '_')}",
                        element_type=elem_type,
                        bbox=norm_bbox,
                        center=(center_x, center_y),
                        text=txt,
                        suggested_action=action,
                        confidence=min(1.0, item.confidence + 0.1),
                        details={"source": "ocr", "raw_score": item.confidence}
                    ))
            except Exception as e:
                logger.debug("Explorer OCR scan notice: %s", e)

        # ----------------------------------------------------
        # 2. Contour / Shape-driven visual button discovery
        # ----------------------------------------------------
        if scan_mode in ("all", "shapes", "buttons"):
            try:
                if hasattr(cv2, "cvtColor") and hasattr(cv2, "threshold") and hasattr(cv2, "findContours"):
                    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if len(crop.shape) == 3 else crop
                    _, thresh = cv2.threshold(gray, 60, 255, cv2.THRESH_BINARY)
                    cnt_res = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                    contours = cnt_res[0] if (isinstance(cnt_res, (list, tuple)) and len(cnt_res) == 2) else (cnt_res[1] if isinstance(cnt_res, (list, tuple)) and len(cnt_res) == 3 else cnt_res)
                    if isinstance(contours, tuple):
                        contours = list(contours)
                    if not isinstance(contours, list):
                        contours = []

                    for c_idx, cnt in enumerate(contours[:20]):
                        if isinstance(cnt, (list, tuple)) and len(cnt) == 4:
                            bx, by, bw, bh = cnt
                        else:
                            bx, by, bw, bh = cv2.boundingRect(cnt)
                        if bw == crop_w and bh == crop_h:
                            continue
                        # Filter by realistic button dimensions (width 40-400px, height 15-80px)
                        if 40 <= bw <= 400 and 15 <= bh <= 80:
                            abs_x = crop_x + bx
                            abs_y = crop_y + by
                            norm_b = NormalizedRect(
                                x=round(abs_x / w, 4),
                                y=round(abs_y / h, 4),
                                width=round(bw / w, 4),
                                height=round(bh / h, 4)
                            )
                            c_x = round(norm_b.x + norm_b.width / 2.0, 4)
                            c_y = round(norm_b.y + norm_b.height / 2.0, 4)

                            # Deduplicate with existing text items
                            overlap = any(
                                abs(c.center[0] - c_x) < 0.03 and abs(c.center[1] - c_y) < 0.03
                                for c in candidates
                            )
                            if not overlap:
                                candidates.append(UIElementCandidate(
                                    candidate_id=f"elem_shape_{c_idx}_{bx}_{by}",
                                    element_type="button",
                                    bbox=norm_b,
                                    center=(c_x, c_y),
                                    text=None,
                                    suggested_action="click_target",
                                    confidence=0.70,
                                    details={"source": "contour", "pixel_dimensions": [bw, bh]}
                                ))
            except Exception as e:
                logger.debug("Explorer shape scan notice: %s", e)

        return candidates

    @staticmethod
    def _probe_number(value: Any, minimum: float, maximum: float, name: str) -> float:
        try:
            if isinstance(value, bool):
                raise ValueError
            number = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise SafetyViolationError(f"Probe {name} must be a finite number.") from exc
        if not math.isfinite(number) or not minimum <= number <= maximum:
            raise SafetyViolationError(f"Probe {name} must be between {minimum} and {maximum}.")
        return number

    def _prepare_probe(self, action: str, params: Dict[str, Any]) -> Tuple[Dict[str, Any], float]:
        """Validates parameters and estimates minimum practical dispatch budget."""
        if action in ("press_keys", "press_key"):
            keys = params.get("keys") or ([params["key"]] if "key" in params else [])
            if not isinstance(keys, list) or not 1 <= len(keys) <= 6 or any(
                not isinstance(key, str) or len(key) > 64 for key in keys
            ):
                raise SafetyViolationError("Probe requires 1 to 6 explicit key tokens.")
            groups = self.input_controller.normalize_and_validate_keys(keys)
            if not groups or any(not 1 <= len(group) <= 4 for group in groups):
                raise SafetyViolationError("Probe key combinations may contain at most four keys.")
            duration = int(self._probe_number(params.get("duration_ms", 50), 0, 2_000, "duration_ms"))
            estimated = sum(max(0.02, duration / 1_000) + 0.02 + 0.01 * (len(group) - 1) for group in groups)
            return {"keys": keys, "duration_ms": duration}, estimated

        if action in ("click_target", "click", "hover", "hover_target"):
            if "x" not in params or "y" not in params:
                raise SafetyViolationError("Probe click/hover requires explicit normalized x and y coordinates.")
            coords = {
                "norm_x": self._probe_number(params["x"], 0.0, 1.0, "x"),
                "norm_y": self._probe_number(params["y"], 0.0, 1.0, "y"),
            }
            if action in ("click_target", "click"):
                button = params.get("button", "left")
                if button not in ("left", "right", "middle"):
                    raise SafetyViolationError("Probe click button must be left, right, or middle.")
                clicks = self._probe_number(params.get("clicks", 1), 1, 5, "clicks")
                if not clicks.is_integer():
                    raise SafetyViolationError("Probe clicks must be an integer.")
                return {**coords, "button": button, "clicks": int(clicks)}, 0.05 * (1 + clicks)
            duration = int(self._probe_number(params.get("duration_ms", 400), 0, 2_000, "duration_ms"))
            return {**coords, "duration_ms": duration}, max(0.01, duration / 1_000)

        if action == "wait":
            duration_s = self._probe_number(params.get("duration_s", 0.2), 0.05, 2.0, "duration_s")
            return {"duration_s": duration_s}, duration_s

        raise ValueError(f"Disallowed probe action: '{action}'. Allowed: press_keys, press_key, click_target, click, hover, wait")

    @staticmethod
    def _check_probe_deadline(deadline: float, phase: str, required_s: float = 0.0) -> None:
        if deadline - time.perf_counter() <= required_s:
            raise _ProbeTimeout(phase)

    def _require_probe_dispatch_target(self, win: Any) -> None:
        self.capture_manager.require_attached_window(self.input_controller.window_manager, win)

    def _capture_probe_frame(self, win: Any) -> np.ndarray:
        frame = self.capture_manager.capture_attached_window(self.input_controller.window_manager, win)
        if not isinstance(frame, np.ndarray) or frame.ndim != 3 or frame.shape[2] != 3 or not frame.size:
            raise SafetyViolationError("Probe capture returned no usable BGR frame.")
        # A backend may reuse its capture buffer. Keep independent snapshots.
        return frame.copy()

    def _dispatch_probe(self, action: str, kwargs: Dict[str, Any], dry_run: bool, win: Any) -> Tuple[bool, Optional[str]]:
        bound = {"target_window": win} if win is not None and not dry_run else {}
        if action in ("press_keys", "press_key"):
            result = self.input_controller.press_keys(**kwargs, dry_run=dry_run, **bound)
            if getattr(result, "dry_run", False) and not dry_run:
                return False, "Input entered dry-run before dispatch."
            return result.success, None if result.success else result.message
        if action in ("click_target", "click"):
            result = self.input_controller.click(**kwargs, dry_run=dry_run, **bound)
            accepted = result.executed or (dry_run and result.dry_run and result.status == "success")
            return accepted, None if accepted else result.reason
        if action in ("hover", "hover_target"):
            _, _, executed = self.input_controller.hover(**kwargs, dry_run=dry_run, **bound)
            return executed or dry_run, None if executed or dry_run else "Hover failed"
        if not dry_run:
            time.sleep(kwargs["duration_s"])
        return True, None

    def _observe_probe(
        self, win: Any, baseline: np.ndarray, roi: Optional[NormalizedRect], deadline: float,
    ) -> _ProbeObservation:
        """Wait for a stable change, or observe an unchanged tail through the deadline."""
        candidate = None
        candidate_since = 0.0
        unchanged_anchor = None
        unchanged_since = None
        last_sample_time = None
        last_delta = 0.0
        saw_change = False

        while (remaining := deadline - time.perf_counter()) > 0:
            time.sleep(min(_PROBE_POLL_S, remaining))
            if time.perf_counter() >= deadline:
                break
            safety_manager.check_safety()
            frame = self._capture_probe_frame(win)
            self._check_probe_deadline(deadline, "observation")
            if frame.shape != baseline.shape:
                raise SafetyViolationError("Probe frame dimensions changed; no comparable post-action observation.")
            delta = self.verifier.verify_selection(
                pre_frame=baseline, post_frame=frame, roi=roi, min_diff_ratio=_PROBE_CHANGE_RATIO,
            ).diff_ratio
            self._check_probe_deadline(deadline, "observation")
            last_sample_time = time.perf_counter()
            last_delta = delta

            if delta >= _PROBE_CHANGE_RATIO:
                saw_change = True
                unchanged_anchor = unchanged_since = None
                if candidate is not None:
                    drift = self.verifier.verify_selection(
                        pre_frame=candidate, post_frame=frame, roi=roi, min_diff_ratio=_PROBE_STABILITY_RATIO,
                    ).diff_ratio
                    self._check_probe_deadline(deadline, "observation")
                    if drift < _PROBE_STABILITY_RATIO:
                        if last_sample_time - candidate_since + 1e-9 >= _PROBE_STABLE_S:
                            outcome = "major_state_transition" if delta >= 0.08 else "localized_ui_update"
                            return _ProbeObservation(outcome, delta, frame, diff_measured=True)
                        continue
                candidate, candidate_since = frame, last_sample_time
            else:
                candidate = None
                if unchanged_anchor is not None:
                    drift = self.verifier.verify_selection(
                        pre_frame=unchanged_anchor, post_frame=frame, roi=roi, min_diff_ratio=_PROBE_STABILITY_RATIO,
                    ).diff_ratio
                    self._check_probe_deadline(deadline, "observation")
                    if drift < _PROBE_STABILITY_RATIO:
                        continue
                unchanged_anchor, unchanged_since = frame, last_sample_time

        if (
            unchanged_since is not None and last_sample_time is not None
            and last_sample_time - unchanged_since + 1e-9 >= _PROBE_STABLE_S
        ):
            return _ProbeObservation(
                "no_visual_change", last_delta, transient_change=saw_change, diff_measured=True,
            )
        return _ProbeObservation("timeout", transient_change=saw_change)

    def _extract_probe_text(
        self, baseline: np.ndarray, post: np.ndarray, roi: Optional[NormalizedRect], deadline: float,
    ) -> List[str]:
        self._check_probe_deadline(deadline, "ocr", required_s=_PROBE_OCR_MIN_BUDGET_S)
        before = self.ocr_engine.read_text(baseline, roi=roi, event_tag="probe_baseline_check")
        self._check_probe_deadline(deadline, "ocr", required_s=_PROBE_OCR_MIN_BUDGET_S)
        after = self.ocr_engine.read_text(post, roi=roi, event_tag="probe_post_check")
        self._check_probe_deadline(deadline, "ocr")

        seen = {" ".join(item.text.split()).casefold() for item in before.items}
        found = []
        for item in after.items:
            text = " ".join(item.text.split())
            key = text.casefold()
            if len(text) > 1 and key not in seen:
                found.append(text)
                seen.add(key)
                if len(found) == 5:
                    break
        return found

    def probe_action(
        self,
        action: str,
        params: Optional[Dict[str, Any]] = None,
        expected_roi: Optional[NormalizedRect] = None,
        timeout_s: float = 5.0,
        dry_run: bool = False,
        revert_on_fail: bool = False,
        game_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Runs one bounded probe. In-flight native calls cannot be forcibly interrupted."""
        t0 = time.perf_counter()
        safety_manager.check_safety()
        p = params or {}
        act_norm = action.lower().strip()
        timeout = self._probe_number(timeout_s, PROBE_TIMEOUT_MIN_S, PROBE_TIMEOUT_MAX_S, "timeout_s")
        kwargs, estimated_dispatch_s = self._prepare_probe(act_norm, p)
        if expected_roi is not None:
            x, y, width, height = (
                self._probe_number(getattr(expected_roi, field), 0.0, 1.0, f"expected_roi.{field}")
                for field in ("x", "y", "width", "height")
            )
            if width <= 0 or height <= 0 or x + width > 1.0 + 1e-9 or y + height > 1.0 + 1e-9:
                raise SafetyViolationError("Probe expected_roi must have nonzero area inside the window.")
            expected_roi = NormalizedRect(x, y, width, height)
        if self.probe_count >= self.max_probe_depth:
            raise SafetyViolationError(
                f"Exploration probe budget exceeded ({self.probe_count}/{self.max_probe_depth} steps). "
                "Review observations or reset probe budget before continuing."
            )
        self.probe_count += 1
        step = self.probe_count

        effective_dry_run = self.input_controller.is_dry_run_active(dry_run)
        deadline = t0 + timeout
        win = None
        baseline = None
        evidence_game_id = game_id or self.active_game_id or "generic"
        dispatch_status = "not_attempted"
        dispatch_error = None
        observation_error = None
        timeout_phase = None
        new_text = []
        ocr_status = "not_attempted"
        observation = _ProbeObservation("blocked")

        phase = "baseline_capture" if not effective_dry_run else "dispatch"
        try:
            if not effective_dry_run:
                win = self.input_controller.window_manager.current_window
                if win is not None:
                    attached_id = game_id_for_window(win)
                    if game_id is not None and game_id != attached_id:
                        raise SafetyViolationError("Requested game_id does not match the attached probe target.")
                    evidence_game_id = attached_id
                    safety_manager.validate_process(win.process_name)
                self._check_probe_deadline(deadline, phase)
                baseline = self._capture_probe_frame(win)
                self._check_probe_deadline(deadline, phase)
                if expected_roi is not None:
                    box = expected_roi.to_pixel(baseline.shape[1], baseline.shape[0])
                    if box.x >= baseline.shape[1] or box.y >= baseline.shape[0]:
                        raise SafetyViolationError("Probe expected_roi rounds to an empty pixel region.")
                phase = "target_check"
                self._require_probe_dispatch_target(win)
                self._check_probe_deadline(deadline, phase)
            phase = "dispatch"
            # Do not start an input when there is insufficient time even for
            # the expected dispatch and the minimum observation window.
            required_s = 0.0 if effective_dry_run else estimated_dispatch_s + _PROBE_POLL_S + _PROBE_STABLE_S
            self._check_probe_deadline(deadline, phase, required_s=required_s)
            safety_manager.check_safety()
            dispatch_status = "failed_or_partial"
            accepted, dispatch_error = self._dispatch_probe(act_norm, kwargs, effective_dry_run, win)
            if accepted:
                dispatch_status = "simulation_accepted" if effective_dry_run else "completed"
            self._check_probe_deadline(deadline, phase)

            if not accepted:
                observation = _ProbeObservation("execution_error")
            elif effective_dry_run:
                observation = _ProbeObservation("simulated")
            else:
                phase = "observation"
                observation = self._observe_probe(win, baseline, expected_roi, deadline)
                if observation.outcome == "timeout":
                    timeout_phase = phase
                if observation.stable_frame is not None:
                    phase = "ocr"
                    try:
                        new_text = self._extract_probe_text(baseline, observation.stable_frame, expected_roi, deadline)
                        ocr_status = "complete"
                    except _ProbeTimeout:
                        timeout_phase = phase
                        ocr_status = "deadline"
                    except (EmergencyStopTriggered, SafetyViolationError):
                        raise
                    except Exception:
                        ocr_status = "error"
                        logger.debug("Probe OCR comparison failed", exc_info=True)
        except _ProbeTimeout as exc:
            timeout_phase = exc.phase
            observation = _ProbeObservation("timeout")
        except EmergencyStopTriggered:
            raise
        except SafetyViolationError as exc:
            if phase in ("dispatch", "ocr"):
                raise
            observation_error = str(exc)
            observation = _ProbeObservation("observation_error" if phase == "observation" else "blocked")
        except Exception as exc:
            if phase in ("baseline_capture", "target_check", "observation"):
                observation_error = str(exc)
                observation = _ProbeObservation("observation_error" if phase == "observation" else "blocked")
            else:
                dispatch_error = str(exc)
                observation = _ProbeObservation("execution_error")
            logger.debug("Probe %s failed", phase, exc_info=True)

        latency_ms = (time.perf_counter() - t0) * 1000.0
        visual_changed = observation.stable_frame is not None
        diff_ratio = round(observation.diff_ratio, 4)
        if self.storage is not None and (not effective_dry_run or self.storage.db.simulation) and observation.outcome != "blocked" and time.perf_counter() < deadline:
            try:
                self.storage.record_action_history(
                    game_id=evidence_game_id,
                    action_name=f"probe_{act_norm}",
                    params=p,
                    success=visual_changed,
                    source="synthetic_fixture" if effective_dry_run else "live",
                    reason=observation.outcome,
                    duration_ms=latency_ms,
                )
            except Exception as exc:
                logger.debug("Failed to record probe action history: %s", exc)

        evidence_level = "simulated" if effective_dry_run else "observed" if observation.diff_measured else "none"
        return {
            "action": action,
            "params": p,
            "executed": dispatch_status == "completed",
            "dispatch_status": dispatch_status,
            "dry_run": effective_dry_run,
            "simulation_accepted": observation.outcome == "simulated",
            "evidence_level": evidence_level,
            "visual_change_detected": visual_changed,
            "verification_passed": visual_changed,  # Legacy visual check, not causal validation.
            "diff_ratio": diff_ratio,
            "visual_diff_measured": observation.diff_measured,
            "outcome": observation.outcome,
            "execution_error": dispatch_error,
            "observation_error": observation_error,
            "timeout_s": timeout,
            "timed_out": timeout_phase is not None,
            "timeout_phase": timeout_phase,
            "transient_change_detected": observation.transient_change,
            "empirical_evidence": {
                "evidence_level": evidence_level,
                "diff_ratio": diff_ratio,
                "visual_diff_measured": observation.diff_measured,
                "visual_changed": visual_changed,
                "outcome": observation.outcome,
                "new_text": new_text,
                "ocr_status": ocr_status,
            },
            "new_text_detected": new_text,
            "latency_ms": round(latency_ms, 2),
            "reverted": False,
            "revert_status": "not_supported_without_verified_undo" if revert_on_fail else "not_requested",
            "probe_step": step,
            "message": f"Probe {action}: outcome={observation.outcome}, diff_ratio={diff_ratio}",
        }

    probe_step = probe_action
