"""
Tests for perception pipeline: OCR on small UI text, ROI cropping coordinate preservation,
RapidOCRLite, and pluggable OCR engine factory.
"""

import numpy as np
import cv2
import pytest
from ugm.core.ocr import RapidOCREngine, RapidOCRLiteEngine, MockOCREngine, get_ocr_engine
from ugm.core.types import NormalizedRect, OCRItem, BoundingBox


def create_synthetic_ui_frame(w=1920, h=1080) -> np.ndarray:
    """Creates a dark UI frame with specific text at known coordinates."""
    frame = np.full((h, w, 3), 35, dtype=np.uint8)

    # Topbar background
    cv2.rectangle(frame, (0, 0), (w, 50), (45, 45, 50), -1)

    # 1. Normal UI text
    cv2.putText(frame, "INFANTRY DIVISION", (200, 35), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (240, 240, 240), 2)

    # 2. Small UI text (e.g. 12px height number badge)
    cv2.putText(frame, "150", (80, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)

    # 3. Small label in bottom dialog
    cv2.rectangle(frame, (500, 400), (900, 700), (55, 55, 60), -1)
    cv2.putText(frame, "PRODUCTION QUEUE", (520, 440), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (220, 220, 220), 2)
    cv2.putText(frame, "Batch 42", (520, 480), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (200, 200, 200), 1)

    return frame


def test_ocr_roi_cropping_and_coordinate_mapping():
    frame = create_synthetic_ui_frame()
    h, w = frame.shape[:2]

    engine = RapidOCREngine(min_score=0.40)

    # Define tight ROI around "INFANTRY DIVISION"
    # Expected area: x in [180, 500], y in [10, 45]
    roi = NormalizedRect(
        x=180.0 / w,
        y=10.0 / h,
        width=350.0 / w,
        height=35.0 / h
    )

    res = engine.read_text(frame, roi=roi, event_tag="test_roi_mapping")
    assert res.items, "Should detect text in ROI"
    assert "INFANTRY" in res.full_text.upper()

    # Verify detected bounding box is mapped back to the 1920x1080 coordinate space, NOT the cropped ROI space!
    item = res.items[0]
    bbox = item.bbox_pixel
    assert bbox.x >= 180, f"Box x ({bbox.x}) should be offset by ROI crop origin (>=180)"
    assert bbox.y >= 10, f"Box y ({bbox.y}) should be offset by ROI crop origin (>=10)"
    assert bbox.right <= 540
    assert bbox.bottom <= 55

    # Verify normalized box
    assert 0.0 <= item.bbox_normalized.x <= 1.0
    assert 0.0 <= item.bbox_normalized.y <= 1.0


def test_ocr_small_ui_text():
    frame = create_synthetic_ui_frame()
    h, w = frame.shape[:2]

    engine = RapidOCREngine(min_score=0.40)

    # Target small number badge "150" at x=80, y=32 (height is ~18px)
    roi_small = NormalizedRect(
        x=60.0 / w,
        y=15.0 / h,
        width=80.0 / w,
        height=25.0 / h
    )

    res = engine.read_text(frame, roi=roi_small, event_tag="test_small_text")
    assert "150" in res.full_text, f"Small text '150' should be recognized, got '{res.full_text}'"

    # Verify bounding box is valid and inside ROI bounds
    for item in res.items:
        assert item.bbox_pixel.x >= 60
        assert item.bbox_pixel.y >= 15


def test_rapidocr_lite_engine():
    engine = RapidOCRLiteEngine(min_score=0.45, intra_op_threads=2)
    assert engine.intra_op_threads == 2
    assert engine.use_cls is False


def test_ocr_engine_factory():
    eng_rapid = get_ocr_engine("rapidocr")
    assert isinstance(eng_rapid, RapidOCREngine)

    eng_lite = get_ocr_engine("rapidocr_lite")
    assert isinstance(eng_lite, RapidOCRLiteEngine)

    eng_mock = get_ocr_engine("mock")
    assert isinstance(eng_mock, MockOCREngine)

    with pytest.raises(ValueError, match="Unknown OCR backend"):
        get_ocr_engine("unsupported_backend_xyz")
