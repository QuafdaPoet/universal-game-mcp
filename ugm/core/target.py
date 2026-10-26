"""
Universal Game MCP (UGM) - Target Selection & Action Verification Engine
Accurately detects candidate UI targets (icons, buttons, text, unit portraits),
disambiguates via tooltip inspection or visual features, rejects ambiguous targets,
and verifies visual state change before confirming success.
"""

from __future__ import annotations
import time
import re
import base64
import logging
from typing import Optional, List, Tuple, Dict, Any
import numpy as np
import cv2

from .types import (
    NormalizedRect,
    BoundingBox,
    TargetStatus,
    TargetCandidate,
    TargetResolution,
    VisionAgentEscalation,
    ClickResult,
    DetectionItem,
    OCRItem
)
from .capture import CaptureManager
from .ocr import BaseOCREngine
from .icon import IconDetector
from .input import InputController
from .verifier import ActionVerifier

logger = logging.getLogger("ugm.target")


class TargetResolver:
    """
    Locates and disambiguates UI targets across visual features and text.
    """

    def __init__(
        self,
        icon_detector: IconDetector,
        ocr_engine: BaseOCREngine,
        verifier: ActionVerifier,
        skill_packs: Optional[Dict[str, Any]] = None
    ):
        self.icon_detector = icon_detector
        self.ocr_engine = ocr_engine
        self.verifier = verifier
        self.skill_packs: Dict[str, Any] = skill_packs or {}

    def resolve_target(
        self,
        frame: np.ndarray,
        icon_name: Optional[str] = None,
        template_image: Optional[np.ndarray] = None,
        text_pattern: Optional[str] = None,
        search_roi: Optional[NormalizedRect] = None,
        threshold: float = 0.80,
        score_tolerance: float = 0.05,
        hover_verification: bool = False,
        expected_tooltip: Optional[str] = None,
        selector_id: Optional[str] = None,
        dry_run: Optional[bool] = None
    ) -> TargetResolution:
        """
        Locates target element, rejects ambiguity, and verifies features.
        """
        is_dry_run = self.verifier.input_controller.is_dry_run_active(dry_run)

        # Resolve selector definition from loaded skill packs if selector_id is provided
        if selector_id and self.skill_packs:
            for pack in self.skill_packs.values():
                selectors_dict = getattr(pack, "selectors", {})
                if selector_id in selectors_dict:
                    sel = selectors_dict[selector_id]
                    if not icon_name and getattr(sel, "template_file", None):
                        icon_name = selector_id
                    if not text_pattern and getattr(sel, "text_pattern", None):
                        text_pattern = sel.text_pattern
                    if not search_roi and getattr(sel, "search_roi", None):
                        search_roi = sel.search_roi
                    if getattr(sel, "threshold", None) is not None:
                        threshold = sel.threshold
                    if getattr(sel, "hover_verification", None):
                        hover_verification = sel.hover_verification
                    if getattr(sel, "expected_tooltip_contains", None):
                        expected_tooltip = sel.expected_tooltip_contains
                    break
        orig_h, orig_w = frame.shape[:2]
        candidates: List[TargetCandidate] = []

        # 1. Icon / Template matching candidates
        if icon_name or template_image is not None:
            try:
                det_res = self.icon_detector.locate_icon(
                    image=frame,
                    template_name=icon_name,
                    template_image=template_image,
                    roi=search_roi,
                    threshold=threshold,
                    score_tolerance=score_tolerance
                )
                for idx, item in enumerate(det_res.items):
                    cand = TargetCandidate(
                        candidate_id=f"icon_{idx}",
                        candidate_type="icon",
                        confidence=item.confidence,
                        bbox_pixel=item.bbox_pixel,
                        bbox_normalized=item.bbox_normalized,
                        center_normalized=item.center_normalized,
                        text=None,
                        details={"icon_name": item.icon_name}
                    )
                    candidates.append(cand)
            except Exception as e:
                logger.warning("Error running icon detection during target resolution: %s", e)

        # 2. OCR Text candidates (if text_pattern is provided)
        if text_pattern:
            try:
                ocr_roi = search_roi
                ocr_res = self.ocr_engine.read_text(
                    frame,
                    roi=ocr_roi,
                    event_tag=f"target_resolve_{selector_id or 'text'}"
                )
                regex = re.compile(text_pattern, re.IGNORECASE)
                for idx, o_item in enumerate(ocr_res.items):
                    if regex.search(o_item.text):
                        cx = round(o_item.bbox_normalized.x + o_item.bbox_normalized.width / 2.0, 4)
                        cy = round(o_item.bbox_normalized.y + o_item.bbox_normalized.height / 2.0, 4)
                        cand = TargetCandidate(
                            candidate_id=f"text_{idx}",
                            candidate_type="text",
                            confidence=o_item.confidence,
                            bbox_pixel=o_item.bbox_pixel,
                            bbox_normalized=o_item.bbox_normalized,
                            center_normalized=(cx, cy),
                            text=o_item.text,
                            details={"matched_pattern": text_pattern}
                        )
                        candidates.append(cand)
            except Exception as e:
                logger.warning("Error running OCR during target resolution: %s", e)

        # Evaluate candidate findings
        if not candidates:
            return TargetResolution(
                status=TargetStatus.NOT_FOUND.value,
                selector_id=selector_id,
                reason=f"No matching visual or text candidates found for selector '{selector_id or 'query'}'.",
                all_candidates=[]
            )

        # Sort candidates by confidence descending
        candidates.sort(key=lambda c: c.confidence, reverse=True)

        # Check for ambiguity if more than one candidate exists
        if len(candidates) > 1:
            top_score = candidates[0].confidence
            second_score = candidates[1].confidence

            # If scores are close within tolerance, target is potentially ambiguous
            if second_score >= top_score * (1.0 - score_tolerance):
                # Disambiguation attempt: Hover and inspect tooltips if requested
                if hover_verification and expected_tooltip:
                    logger.info("Attempting tooltip disambiguation for %d candidates...", len(candidates))
                    resolved_cand = None
                    for c in candidates[:3]:  # probe up to top 3
                        nx, ny = c.center_normalized
                        hover_res = self.verifier.hover_and_read_tooltip(
                            norm_x=nx,
                            norm_y=ny,
                            duration_ms=250,
                            dry_run=is_dry_run
                        )
                        c.tooltip_text = hover_res.tooltip_text
                        if hover_res.tooltip_text and expected_tooltip.lower() in hover_res.tooltip_text.lower():
                            resolved_cand = c
                            break

                    if resolved_cand:
                        return TargetResolution(
                            status=TargetStatus.SUCCESS.value,
                            selector_id=selector_id,
                            target_candidate=resolved_cand,
                            all_candidates=candidates,
                            coordinates_normalized=resolved_cand.center_normalized,
                            evidence={
                                "disambiguated_by": "tooltip",
                                "tooltip_text": resolved_cand.tooltip_text,
                                "confidence": resolved_cand.confidence
                            },
                            reason="Disambiguated candidate uniquely via tooltip hover."
                        )

                # Cannot disambiguate deterministically -> REJECT ambiguous targets
                escalation = VisionAgentEscalation(
                    needed=True,
                    reason=(
                        f"Ambiguous target: {len(candidates)} candidates found with top scores "
                        f"({top_score:.3f} vs {second_score:.3f}). Exact target cannot be determined."
                    ),
                    target_type="ui_element",
                    candidates=[c.to_dict() for c in candidates[:4]],
                    suggested_prompt=(
                        f"Help select correct target among {len(candidates)} candidates for "
                        f"selector '{selector_id or 'target'}'."
                    ),
                    roi_normalized=search_roi
                )

                return TargetResolution(
                    status=TargetStatus.AMBIGUOUS.value,
                    selector_id=selector_id,
                    target_candidate=None,
                    all_candidates=candidates,
                    escalation=escalation,
                    reason="Target selection rejected due to competing ambiguous candidates."
                )

        # Single unambiguous candidate or clear top winner
        winner = candidates[0]

        # Optional single-candidate tooltip verification
        if hover_verification and expected_tooltip:
            if is_dry_run:
                winner.tooltip_text = f"[DRY-RUN] Tooltip simulated for '{expected_tooltip}'"
            else:
                nx, ny = winner.center_normalized
                hover_res = self.verifier.hover_and_read_tooltip(
                    norm_x=nx,
                    norm_y=ny,
                    duration_ms=250,
                    dry_run=is_dry_run
                )
                winner.tooltip_text = hover_res.tooltip_text
                if not hover_res.tooltip_text or expected_tooltip.lower() not in hover_res.tooltip_text.lower():
                    return TargetResolution(
                        status=TargetStatus.FAILED.value,
                        selector_id=selector_id,
                        target_candidate=winner,
                        all_candidates=candidates,
                        reason=(
                            f"Tooltip verification failed: expected '{expected_tooltip}', "
                            f"found '{hover_res.tooltip_text}'"
                        )
                    )

        return TargetResolution(
            status="found",
            selector_id=selector_id,
            target_candidate=winner,
            all_candidates=candidates,
            coordinates_normalized=winner.center_normalized,
            evidence={
                "confidence": winner.confidence,
                "candidate_type": winner.candidate_type,
                "matched_text": winner.text,
                "tooltip": winner.tooltip_text
            },
            reason="Unambiguous target identified successfully."
        )


class TargetExecutor:
    """
    Executes actions on identified targets and verifies visual state change.
    Never reports success based solely on whether a mouse click was sent.
    """

    def __init__(
        self,
        resolver: TargetResolver,
        capture_manager: CaptureManager,
        input_controller: InputController,
        verifier: ActionVerifier
    ):
        self.resolver = resolver
        self.capture_manager = capture_manager
        self.input_controller = input_controller
        self.verifier = verifier

    def click_and_verify(
        self,
        norm_x: Optional[float] = None,
        norm_y: Optional[float] = None,
        target_resolution: Optional[TargetResolution] = None,
        button: str = "left",
        clicks: int = 1,
        dry_run: bool = False,
        expected_text: Optional[str] = None,
        verification_roi: Optional[NormalizedRect] = None,
        min_diff_ratio: float = 0.005,
        timeout_s: float = 5.0
    ) -> ClickResult:
        """
        Executes click on target and verifies that intended visual state change succeeded.
        """
        t0 = time.perf_counter()
        effective_dry_run = self.input_controller.is_dry_run_active(dry_run)
        win = self.input_controller.window_manager.current_window

        # Handle target resolution if provided
        if target_resolution is not None:
            if target_resolution.status in (TargetStatus.NOT_FOUND.value, TargetStatus.AMBIGUOUS.value, "rejected"):
                return ClickResult(
                    x_normalized=0.0,
                    y_normalized=0.0,
                    x_pixel=0,
                    y_pixel=0,
                    button=button,
                    clicks=clicks,
                    dry_run=effective_dry_run,
                    executed=False,
                    verification_passed=False,
                    reason=f"Action rejected: {target_resolution.reason}",
                    latency_ms=(time.perf_counter() - t0) * 1000.0,
                    status=target_resolution.status,
                    evidence=target_resolution.evidence
                )

            if target_resolution.coordinates_normalized:
                norm_x, norm_y = target_resolution.coordinates_normalized

        if norm_x is None or norm_y is None:
            return ClickResult(
                x_normalized=0.0,
                y_normalized=0.0,
                x_pixel=0,
                y_pixel=0,
                button=button,
                clicks=clicks,
                dry_run=effective_dry_run,
                executed=False,
                verification_passed=False,
                reason="Coordinates could not be resolved and no target specified.",
                latency_ms=(time.perf_counter() - t0) * 1000.0,
                status=TargetStatus.NOT_FOUND.value
            )

        # Step 1: Capture pre-action baseline
        pre_frame = self.capture_manager.capture(window=win)

        # Step 2: Dispatch input click
        click_res = self.input_controller.click(
            norm_x=norm_x,
            norm_y=norm_y,
            button=button,
            clicks=clicks,
            dry_run=effective_dry_run
        )

        if effective_dry_run or click_res.dry_run:
            click_res.status = TargetStatus.SUCCESS.value
            click_res.verification_passed = True
            click_res.evidence = {"dry_run_simulated": True}
            return click_res

        if not click_res.executed:
            # Click was blocked by safety manager, emergency stop, or out-of-bounds
            click_res.status = TargetStatus.FAILED.value
            return click_res

        # Step 3: Wait stabilization interval and capture post-action frame
        time.sleep(0.08)
        post_frame = self.capture_manager.capture(window=win)

        # Step 4: Verify state change
        ver_res = self.verifier.verify_selection(
            pre_frame=pre_frame,
            post_frame=post_frame,
            roi=verification_roi,
            expected_text=expected_text,
            min_diff_ratio=min_diff_ratio
        )

        click_res.verification_passed = ver_res.verified
        click_res.evidence = {
            "diff_ratio": ver_res.diff_ratio,
            "min_diff_required": min_diff_ratio,
            "verification_details": ver_res.details
        }

        if ver_res.verified:
            click_res.status = TargetStatus.SUCCESS.value
            click_res.reason = f"Target clicked and state verified successfully ({ver_res.message})."
        else:
            click_res.status = TargetStatus.FAILED.value
            click_res.reason = f"Click dispatched but state change verification failed ({ver_res.message})."

        return click_res
