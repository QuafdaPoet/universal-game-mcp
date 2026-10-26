"""
Universal Game MCP (UGM) - Icon Detection Subsystem
Provides multi-scale template matching with OpenCV, alpha-mask support,
Non-Maximum Suppression (NMS), confidence scoring, and ambiguity detection.
"""

from __future__ import annotations
import time
import logging
from typing import Optional, List, Tuple, Dict, Any
import numpy as np
import cv2

from .types import (
    DetectionItem,
    DetectionResult,
    NormalizedRect,
    BoundingBox,
    MatchMethod
)

logger = logging.getLogger("ugm.icon")


def non_max_suppression_fast(boxes: np.ndarray, overlap_thresh: float = 0.3) -> List[int]:
    """
    Standard fast NMS implementation for bounding boxes [x1, y1, x2, y2, score].
    Returns list of kept indices.
    """
    if len(boxes) == 0:
        return []

    x1 = boxes[:, 0]
    y1 = boxes[:, 1]
    x2 = boxes[:, 2]
    y2 = boxes[:, 3]
    scores = boxes[:, 4]

    area = (x2 - x1 + 1) * (y2 - y1 + 1)
    idxs = np.argsort(scores)[::-1]

    pick: List[int] = []

    while len(idxs) > 0:
        last = len(idxs) - 1
        i = idxs[0]
        pick.append(int(i))

        xx1 = np.maximum(x1[i], x1[idxs[1:]])
        yy1 = np.maximum(y1[i], y1[idxs[1:]])
        xx2 = np.minimum(x2[i], x2[idxs[1:]])
        yy2 = np.minimum(y2[i], y2[idxs[1:]])

        w = np.maximum(0, xx2 - xx1 + 1)
        h = np.maximum(0, yy2 - yy1 + 1)

        overlap = (w * h) / area[idxs[1:]]

        idxs = np.delete(idxs, np.concatenate(([0], np.where(overlap > overlap_thresh)[0] + 1)))

    return pick


class IconDetector:
    """
    OpenCV-based icon and template detector optimized for game UIs.
    Supports multi-scale scanning, masking, and ambiguity flagging.
    """

    def __init__(
        self,
        default_threshold: float = 0.80,
        scales: Optional[List[float]] = None
    ):
        self.default_threshold = default_threshold
        # Support common UI scaling (e.g. 0.8x, 0.9x, 1.0x, 1.1x, 1.2x)
        self.scales = scales or [1.0, 0.9, 1.1, 0.8, 1.2]
        self._templates: Dict[str, np.ndarray] = {}
        self._masks: Dict[str, Optional[np.ndarray]] = {}

    def register_template(
        self,
        name: str,
        template: np.ndarray,
        mask: Optional[np.ndarray] = None
    ) -> None:
        """Registers a named icon template (BGR or BGRA)."""
        if template.shape[-1] == 4:
            # Alpha channel used as mask
            bgr = template[:, :, :3]
            alpha = template[:, :, 3]
            self._templates[name] = bgr
            self._masks[name] = alpha
        else:
            self._templates[name] = template
            self._masks[name] = mask

    def load_template_file(self, name: str, file_path: str) -> None:
        """Loads and registers an icon template from an image file."""
        img = cv2.imread(file_path, cv2.IMREAD_UNCHANGED)
        if img is None:
            raise FileNotFoundError(f"Icon template image not found: {file_path}")
        self.register_template(name, img)

    def locate_icon(
        self,
        image: np.ndarray,
        template_name: Optional[str] = None,
        template_image: Optional[np.ndarray] = None,
        roi: Optional[NormalizedRect] = None,
        threshold: Optional[float] = None,
        multi_scale: bool = True,
        score_tolerance: float = 0.05
    ) -> DetectionResult:
        """
        Locates occurrences of an icon in the target image.
        Detects ambiguity if multiple competing candidates exist.
        """
        t0 = time.perf_counter()
        thresh = threshold if threshold is not None else self.default_threshold

        # Resolve template
        mask = None
        if template_image is not None:
            if template_image.shape[-1] == 4:
                tmpl = template_image[:, :, :3]
                mask = template_image[:, :, 3]
            else:
                tmpl = template_image
            icon_tag = template_name or "custom_template"
        elif template_name is not None:
            if template_name not in self._templates:
                raise KeyError(f"Template '{template_name}' is not registered.")
            tmpl = self._templates[template_name]
            mask = self._masks.get(template_name)
            icon_tag = template_name
        else:
            raise ValueError("Either template_name or template_image must be provided.")

        orig_h, orig_w = image.shape[:2]
        crop_img = image
        crop_offset_x = 0
        crop_offset_y = 0

        # Prioritize CPU: crop ROI before matching
        if roi is not None:
            roi_bbox = roi.to_pixel(orig_w, orig_h)
            crop_offset_x = roi_bbox.x
            crop_offset_y = roi_bbox.y
            crop_img = image[roi_bbox.y:roi_bbox.bottom, roi_bbox.x:roi_bbox.right]

        ch, cw = crop_img.shape[:2]
        th, tw = tmpl.shape[:2]

        if ch < th or cw < tw:
            # Search image smaller than template
            return DetectionResult(
                items=[],
                latency_ms=(time.perf_counter() - t0) * 1000.0,
                is_ambiguous=False,
                threshold=thresh,
                roi_normalized=roi
            )

        candidate_boxes = []  # [x1, y1, x2, y2, score]

        scales_to_try = self.scales if multi_scale else [1.0]

        for s in scales_to_try:
            scaled_tw = int(tw * s)
            scaled_th = int(th * s)

            if scaled_th > ch or scaled_tw > cw or scaled_tw < 5 or scaled_th < 5:
                continue

            scaled_tmpl = cv2.resize(tmpl, (scaled_tw, scaled_th), interpolation=cv2.INTER_AREA if s < 1.0 else cv2.INTER_LINEAR)
            scaled_mask = None
            if mask is not None:
                scaled_mask = cv2.resize(mask, (scaled_tw, scaled_th), interpolation=cv2.INTER_NEAREST)

            if scaled_mask is not None:
                res = cv2.matchTemplate(crop_img, scaled_tmpl, cv2.TM_CCORR_NORMED, mask=scaled_mask)
            else:
                res = cv2.matchTemplate(crop_img, scaled_tmpl, cv2.TM_CCOEFF_NORMED)

            loc = np.where(res >= thresh)
            for pt_y, pt_x in zip(loc[0], loc[1]):
                score = float(res[pt_y, pt_x])
                candidate_boxes.append([pt_x, pt_y, pt_x + scaled_tw, pt_y + scaled_th, score])

        if not candidate_boxes:
            return DetectionResult(
                items=[],
                latency_ms=(time.perf_counter() - t0) * 1000.0,
                is_ambiguous=False,
                threshold=thresh,
                roi_normalized=roi
            )

        boxes_arr = np.array(candidate_boxes, dtype=np.float32)
        keep_idxs = non_max_suppression_fast(boxes_arr, overlap_thresh=0.3)

        items: List[DetectionItem] = []
        for idx in keep_idxs:
            x1, y1, x2, y2, score = boxes_arr[idx]

            # Remap to window coordinates
            full_x1 = int(x1 + crop_offset_x)
            full_y1 = int(y1 + crop_offset_y)
            w = int(x2 - x1)
            h = int(y2 - y1)

            cx = full_x1 + w // 2
            cy = full_y1 + h // 2

            pix_bbox = BoundingBox(x=full_x1, y=full_y1, width=w, height=h)
            norm_bbox = pix_bbox.to_normalized(orig_w, orig_h)

            norm_cx = round(cx / orig_w, 4) if orig_w > 0 else 0.0
            norm_cy = round(cy / orig_h, 4) if orig_h > 0 else 0.0

            item = DetectionItem(
                icon_name=icon_tag,
                confidence=float(score),
                bbox_pixel=pix_bbox,
                bbox_normalized=norm_bbox,
                center_pixel=(cx, cy),
                center_normalized=(norm_cx, norm_cy),
                match_method=MatchMethod.TEMPLATE_CCOEFF_NORMED.value
            )
            items.append(item)

        # Ambiguity check: if 2 or more distinct matches have nearly identical confidence
        is_ambiguous = False
        escalation = None
        status = "found" if items else "not_found"

        if len(items) >= 2:
            top_score = items[0].confidence
            second_score = items[1].confidence
            if second_score >= top_score * (1.0 - score_tolerance):
                is_ambiguous = True
                status = "ambiguous"
                escalation = {
                    "needed": True,
                    "reason": f"Multiple matching candidates with score difference within {score_tolerance * 100:.1f}%",
                    "target_type": "icon",
                    "candidates": [item.to_dict() for item in items[:4]],
                    "suggested_prompt": f"Disambiguate between {len(items)} candidate visual matches for template '{icon_tag}'",
                }

        latency_ms = (time.perf_counter() - t0) * 1000.0

        return DetectionResult(
            items=items,
            latency_ms=latency_ms,
            is_ambiguous=is_ambiguous,
            threshold=thresh,
            roi_normalized=roi,
            status=status,
            escalation=escalation
        )
