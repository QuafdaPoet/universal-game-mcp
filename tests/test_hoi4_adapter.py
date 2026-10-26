"""
Unit tests for HOI4 plugin adapter.
"""

import cv2
import pytest

from ugm.adapters.hoi4.adapter import HOI4Adapter
from ugm.core.ocr import RapidOCREngine
from ugm.core.icon import IconDetector


def test_hoi4_adapter_regions_and_shortcuts():
    adapter = HOI4Adapter()
    assert adapter.game_id == "hoi4"
    assert "Hearts of Iron IV" in adapter.display_name
    assert "hoi4.exe" in adapter.process_names

    regions = adapter.get_ui_regions()
    assert "topbar" in regions
    assert "political_power" in regions
    assert "factories" in regions
    assert "pause_indicator" in regions

    shortcuts = adapter.get_shortcuts()
    assert shortcuts["pause"] == ["space"]
    assert shortcuts["research"] == ["w"]


def test_hoi4_parse_topbar_fixture():
    adapter = HOI4Adapter()
    ocr = RapidOCREngine(min_score=0.40, use_cls=False)
    img = cv2.imread("tests/fixtures/hoi4_topbar.png")

    metrics = adapter.parse_topbar(ocr, img)
    assert "political_power" in metrics
    assert "150" in metrics["political_power"]["text"]
    assert "stability" in metrics
    assert "75" in metrics["stability"]["text"]
