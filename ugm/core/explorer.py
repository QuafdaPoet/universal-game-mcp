"""
Universal Game MCP (UGM) - Autonomous UI Explorer & Probing Engine
Provides generic UI affordance discovery, interactive element scanning,
and bounded Observe-Act-Verify probing for unsupported games.
"""

from __future__ import annotations
import time
import uuid
import logging
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field, asdict
import numpy as np
import cv2

from .types import NormalizedRect, BoundingBox, VerificationResult
from .safety import safety_manager, EmergencyStopTriggered, SafetyViolationError
from .capture import CaptureManager
from .input import InputController
from .verifier import ActionVerifier
from .ocr import BaseOCREngine
from .icon import IconDetector
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from ..memory.storage import PersistentKnowledgeStorage

logger = logging.getLogger("ugm.core.explorer")


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
        storage: Optional[PersistentKnowledgeStorage] = None
    ):
        self.capture_manager = capture_manager
        self.input_controller = input_controller
        self.verifier = verifier
        self.ocr_engine = ocr_engine
        self.icon_detector = icon_detector
        self.storage = storage

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
        if frame is None:
            return candidates

        h, w = frame.shape[:2]
        crop_x = int(roi.x * w) if roi else 0
        crop_y = int(roi.y * h) if roi else 0
        crop_w = int(roi.width * w) if roi else w
        crop_h = int(roi.height * h) if roi else h

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
                crop = frame[crop_y:crop_y + crop_h, crop_x:crop_x + crop_w]
                if hasattr(cv2, "cvtColor") and hasattr(cv2, "threshold") and hasattr(cv2, "findContours"):
                    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if len(crop.shape) == 3 else crop
                    _, thresh = cv2.threshold(gray, 60, 255, cv2.THRESH_BINARY)
                    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

                    for c_idx, cnt in enumerate(contours[:20]):
                        bx, by, bw, bh = cv2.boundingRect(cnt)
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

        # Fallback anchor if no candidates detected (ensures exploration baseline)
        if not candidates and scan_mode in ("all", "buttons"):
            candidates.append(UIElementCandidate(
                candidate_id="elem_anchor_center",
                element_type="button",
                bbox=NormalizedRect(x=0.4, y=0.45, width=0.2, height=0.1),
                center=(0.5, 0.5),
                text=None,
                suggested_action="click_target",
                confidence=0.5,
                details={"source": "fallback_center_anchor"}
            ))

        return candidates

    def probe_action(
        self,
        action: str,
        params: Optional[Dict[str, Any]] = None,
        expected_roi: Optional[NormalizedRect] = None,
        timeout_s: float = 5.0,
        dry_run: bool = False,
        revert_on_fail: bool = False
    ) -> Dict[str, Any]:
        """
        Executes a bounded Observe-Act-Verify probe step.
        Dispatches action, measures visual/textual delta, and classifies result.
        """
        safety_manager.check_safety()
        p = params or {}
        t0 = time.perf_counter()

        # Step 1: Capture baseline pre-action frame
        pre_frame = self.capture_manager.capture()

        # Step 2: Dispatch candidate action safely
        executed = True
        exec_error = None
        act_norm = action.lower().strip()
        try:
            if act_norm in ("press_keys", "press_key"):
                keys = p.get("keys") or ([p["key"]] if "key" in p else [])
                duration = p.get("duration_ms", 50)
                res = self.input_controller.press_keys(keys=keys, duration_ms=duration, dry_run=dry_run)
                executed = res.get("executed", True) if isinstance(res, dict) else getattr(res, "executed", True)
            elif act_norm in ("click_target", "click"):
                x = p.get("x", 0.5)
                y = p.get("y", 0.5)
                button = p.get("button", "left")
                clicks = p.get("clicks", 1)
                res = self.input_controller.click(norm_x=x, norm_y=y, button=button, clicks=clicks, dry_run=dry_run)
                succ = getattr(res, "status", None) == "success" or getattr(res, "executed", False)
                executed = True if (dry_run or succ) else False
                if not executed:
                    exec_error = getattr(res, "reason", "Click failed")
            elif act_norm in ("hover", "hover_target"):
                x = p.get("x", 0.5)
                y = p.get("y", 0.5)
                duration = p.get("duration_ms", 400)
                res = self.input_controller.hover(norm_x=x, norm_y=y, duration_ms=duration, dry_run=dry_run)
                executed = res[2] if isinstance(res, tuple) and len(res) >= 3 else True
            elif act_norm == "wait":
                duration_s = min(2.0, max(0.05, float(p.get("duration_s", 0.2))))
                if not dry_run:
                    time.sleep(duration_s)
                executed = True
            else:
                raise ValueError(f"Disallowed probe action: '{action}'. Allowed: press_keys, press_key, click_target, click, hover, wait")
        except Exception as e:
            executed = False
            exec_error = str(e)
            logger.warning("Probe action '%s' execution error: %s", action, e)

        # Step 3: Stabilization delay
        if not dry_run:
            time.sleep(0.08)

        # Step 4: Capture post-action frame
        post_frame = self.capture_manager.capture()

        # Step 5: Diff & Verification Analysis
        diff_res: VerificationResult = self.verifier.verify_selection(
            pre_frame=pre_frame,
            post_frame=post_frame,
            roi=expected_roi,
            min_diff_ratio=0.005
        )

        diff_ratio = diff_res.diff_ratio
        visual_changed = diff_res.verified

        if dry_run and executed and not exec_error:
            # In dry-run mode, simulate affirmative visual response
            diff_ratio = 0.025
            visual_changed = True

        latency_ms = (time.perf_counter() - t0) * 1000.0

        # Classify outcome
        outcome = "no_visual_change"
        if exec_error:
            outcome = "execution_error"
        elif diff_ratio >= 0.08:
            outcome = "major_state_transition"
        elif diff_ratio >= 0.005:
            outcome = "localized_ui_update"

        # Check for newly appeared text if diff detected
        new_text_found = []
        if visual_changed and not exec_error:
            try:
                post_ocr = self.ocr_engine.read_text(post_frame, roi=expected_roi, event_tag="probe_post_check")
                new_text_found = [i.text for i in post_ocr.items[:5] if len(i.text) > 1]
            except Exception:
                pass

        # Optional Revert on Failure (Safe Retreat)
        reverted = False
        if revert_on_fail and (not visual_changed or exec_error):
            try:
                self.input_controller.press_keys(keys=["escape"], duration_ms=50, dry_run=dry_run)
                reverted = True
            except Exception:
                pass

        return {
            "action": action,
            "params": p,
            "executed": executed,
            "visual_change_detected": visual_changed,
            "verification_passed": visual_changed,
            "diff_ratio": round(diff_ratio, 4),
            "outcome": outcome,
            "execution_error": exec_error,
            "empirical_evidence": {
                "diff_ratio": round(diff_ratio, 4),
                "visual_changed": visual_changed,
                "outcome": outcome,
                "new_text": new_text_found
            },
            "new_text_detected": new_text_found,
            "latency_ms": round(latency_ms, 2),
            "reverted": reverted,
            "message": f"Probe {action}: outcome={outcome}, diff_ratio={round(diff_ratio, 4)}"
        }

    probe_step = probe_action
