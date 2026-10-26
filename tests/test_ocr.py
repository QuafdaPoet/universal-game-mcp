"""
Unit tests for UGM RapidOCR engine (event-driven, CPU-optimized with ROI cropping).
"""

import os
import cv2
import pytest
from ugm.core.ocr import RapidOCREngine, MockOCREngine
from ugm.core.types import NormalizedRect, OCRItem, BoundingBox


def test_rapid_ocr_crop_roi_topbar():
    img_path = "tests/fixtures/hoi4_topbar.png"
    assert os.path.exists(img_path)
    img = cv2.imread(img_path)

    engine = RapidOCREngine(min_score=0.40, use_cls=False)

    # Crop political power ROI
    pp_roi = NormalizedRect(x=0.04, y=0.0, width=0.08, height=0.05)
    res = engine.read_text(img, roi=pp_roi, event_tag="test_pp")

    assert res.engine == "RapidOCR-ONNX"
    assert "150" in res.full_text
    assert len(res.items) >= 1
    item = res.items[0]
    assert item.confidence > 0.5
    assert item.bbox_normalized.x >= 0.0


def test_rapid_ocr_dialog():
    img_path = "tests/fixtures/hoi4_dialog.png"
    img = cv2.imread(img_path)

    engine = RapidOCREngine(min_score=0.40, use_cls=False)

    # Read dialog header
    header_roi = NormalizedRect(x=0.35, y=0.28, width=0.30, height=0.08)
    res = engine.read_text(img, roi=header_roi, event_tag="dialog_header")

    assert "Munich" in res.full_text or "Treaty" in res.full_text


def test_mock_ocr_engine():
    mock_item = OCRItem(
        text="MOCK_TEST",
        confidence=0.99,
        bbox_pixel=BoundingBox(10, 10, 50, 20),
        bbox_normalized=NormalizedRect(0.01, 0.01, 0.05, 0.02)
    )
    engine = MockOCREngine(predefined_results=[mock_item])
    res = engine.read_text(cv2.imread("tests/fixtures/hoi4_topbar.png"))

    assert res.full_text == "MOCK_TEST"
    assert len(res.items) == 1
