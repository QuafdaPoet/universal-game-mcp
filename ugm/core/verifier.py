"""
Universal Game MCP (UGM) - Action Verification Subsystem
Provides visual diffing, state change verification, and tooltip hover-and-read mechanics.
"""

from __future__ import annotations
import time
import logging
from typing import Optional, List, Tuple, Any
import numpy as np
import cv2

from .types import (
    NormalizedRect,
    BoundingBox,
    HoverResult,
    VerificationResult,
    OCRResult
)
from .capture import CaptureManager
from .ocr import BaseOCREngine
from .input import InputController

logger = logging.getLogger("ugm.verifier")


class ActionVerifier:
    """
    Verifies the visual consequences of user or AI actions within the game window.
    """

    def __init__(
        self,
        capture_manager: CaptureManager,
        ocr_engine: BaseOCREngine,
        input_controller: InputController
    ):
        self.capture_manager = capture_manager
        self.ocr_engine = ocr_engine
        self.input_controller = input_controller

    def hover_and_read_tooltip(
        self,
        norm_x: float,
        norm_y: float,
        duration_ms: int = 500,
        dry_run: Optional[bool] = None
    ) -> HoverResult:
        """
        Hovers over target coordinates, detects any newly appeared tooltip window,
        and reads its text via event-driven OCR.
        """
        t0 = time.perf_counter()

        win = self.input_controller.window_manager.current_window
        # Capture pre-hover baseline
        pre_frame = self.capture_manager.capture(window=win)
        h, w = pre_frame.shape[:2]

        # Execute hover
        screen_x, screen_y, executed = self.input_controller.hover(
            norm_x=norm_x,
            norm_y=norm_y,
            duration_ms=duration_ms,
            dry_run=dry_run
        )

        # Capture post-hover frame
        post_frame = self.capture_manager.capture(window=win)

        # Find visual difference to isolate tooltip
        diff = cv2.absdiff(pre_frame, post_frame)
        diff_gray = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
        _, thresh = cv2.threshold(diff_gray, 30, 255, cv2.THRESH_BINARY)

        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        tooltip_detected = False
        tooltip_text = None
        tooltip_bbox_norm: Optional[NormalizedRect] = None

        if contours:
            # Find largest changed contour
            largest = max(contours, key=cv2.contourArea)
            area = cv2.contourArea(largest)

            # Tooltip area heuristic: at least 200 pixels
            if area > 200:
                bx, by, bw, bh = cv2.boundingRect(largest)
                pix_bbox = BoundingBox(x=bx, y=by, width=bw, height=bh)
                tooltip_bbox_norm = pix_bbox.to_normalized(w, h)
                tooltip_detected = True

                # Run event-driven OCR on the tooltip crop
                ocr_res = self.ocr_engine.read_text(
                    post_frame,
                    roi=tooltip_bbox_norm,
                    event_tag="tooltip_read"
                )
                tooltip_text = ocr_res.full_text if ocr_res.items else None

        # Fallback if no clean diff detected (e.g. static mock or dry run): check neighborhood around target
        if not tooltip_detected:
            # Check a 20% x 10% box around target
            box_w = 0.25
            box_h = 0.12
            nb_x = max(0.0, norm_x)
            nb_y = min(1.0 - box_h, norm_y + 0.02)
            search_roi = NormalizedRect(x=nb_x, y=nb_y, width=box_w, height=box_h)

            ocr_res = self.ocr_engine.read_text(
                post_frame,
                roi=search_roi,
                event_tag="tooltip_neighborhood"
            )
            if ocr_res.items:
                tooltip_detected = True
                tooltip_text = ocr_res.full_text
                tooltip_bbox_norm = search_roi

        latency_ms = (time.perf_counter() - t0) * 1000.0

        return HoverResult(
            target_normalized=(norm_x, norm_y),
            target_pixel=(int(norm_x * w), int(norm_y * h)),
            duration_ms=duration_ms,
            tooltip_detected=tooltip_detected,
            tooltip_text=tooltip_text,
            tooltip_bbox=tooltip_bbox_norm,
            latency_ms=latency_ms
        )

    def verify_selection(
        self,
        pre_frame: np.ndarray,
        post_frame: np.ndarray,
        roi: Optional[NormalizedRect] = None,
        expected_text: Optional[str] = None,
        min_diff_ratio: float = 0.005
    ) -> VerificationResult:
        """
        Compares frames before and after an action to confirm state change.
        Optionally verifies that expected text appears in the ROI.
        """
        t0 = time.perf_counter()
        if pre_frame is None or post_frame is None:
            return VerificationResult(
                verified=False,
                diff_ratio=0.0,
                message="Verification failed: baseline pre_frame or post_frame is missing.",
                latency_ms=(time.perf_counter() - t0) * 1000.0,
                details={"error": "missing_frame"}
            )
        h, w = pre_frame.shape[:2]

        crop_pre = pre_frame
        crop_post = post_frame

        if roi is not None:
            bbox = roi.to_pixel(w, h)
            crop_pre = pre_frame[bbox.y:bbox.bottom, bbox.x:bbox.right]
            crop_post = post_frame[bbox.y:bbox.bottom, bbox.x:bbox.right]

        if crop_pre.shape != crop_post.shape:
            return VerificationResult(
                verified=False,
                diff_ratio=1.0,
                message="Pre and post frame dimensions mismatch",
                latency_ms=(time.perf_counter() - t0) * 1000.0
            )

        diff = cv2.absdiff(crop_pre, crop_post)
        diff_gray = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
        changed_pixels = np.count_nonzero(diff_gray > 20)
        total_pixels = max(1, diff_gray.size)
        diff_ratio = float(changed_pixels / total_pixels)

        visual_change_detected = diff_ratio >= min_diff_ratio
        text_verified = True
        found_text = None

        if expected_text is not None:
            ocr_res = self.ocr_engine.read_text(
                post_frame,
                roi=roi,
                event_tag="selection_verification"
            )
            found_text = ocr_res.full_text
            text_verified = expected_text.lower() in found_text.lower()

        verified = visual_change_detected and text_verified
        message = (
            f"Verification {'PASSED' if verified else 'FAILED'}: "
            f"diff_ratio={diff_ratio:.4f} (min={min_diff_ratio}), "
            f"expected_text='{expected_text}', found='{found_text}'"
        )

        return VerificationResult(
            verified=verified,
            diff_ratio=diff_ratio,
            message=message,
            latency_ms=(time.perf_counter() - t0) * 1000.0,
            details={
                "visual_change": visual_change_detected,
                "text_verified": text_verified,
                "found_text": found_text,
            }
        )
