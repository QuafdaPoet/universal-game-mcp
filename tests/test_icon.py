"""
Unit tests for UGM OpenCV icon detector (multi-scale template matching, NMS, ambiguity check).
"""

import os
import cv2
import pytest
from ugm.core.icon import IconDetector
from ugm.core.types import NormalizedRect


def test_icon_detection_civilian_factory():
    img_path = "tests/fixtures/hoi4_topbar.png"
    icon_path = "tests/fixtures/icons/civilian_factory.png"
    assert os.path.exists(img_path)
    assert os.path.exists(icon_path)

    img = cv2.imread(img_path)
    detector = IconDetector(default_threshold=0.85)
    detector.load_template_file("civilian_factory", icon_path)

    res = detector.locate_icon(img, template_name="civilian_factory")

    assert len(res.items) >= 1
    best = res.items[0]
    assert best.icon_name == "civilian_factory"
    assert best.confidence >= 0.85
    assert best.center_normalized[0] > 0.0
    assert best.center_normalized[1] > 0.0
    assert res.is_ambiguous is False


def test_icon_detection_with_roi():
    img = cv2.imread("tests/fixtures/hoi4_topbar.png")
    detector = IconDetector(default_threshold=0.85)
    detector.load_template_file("pause_btn", "tests/fixtures/icons/pause_btn.png")

    # Only look in top-right area
    roi = NormalizedRect(x=0.80, y=0.03, width=0.15, height=0.06)
    res = detector.locate_icon(img, template_name="pause_btn", roi=roi)

    assert len(res.items) >= 1
    assert res.items[0].confidence >= 0.85


def test_missing_template_raises_key_error():
    img = cv2.imread("tests/fixtures/hoi4_topbar.png")
    detector = IconDetector()
    with pytest.raises(KeyError):
        detector.locate_icon(img, template_name="non_existent_icon")
