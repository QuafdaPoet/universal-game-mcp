"""
Unit tests for UGM core types and coordinate conversion math.
"""

import pytest
from ugm.core.types import (
    BoundingBox,
    NormalizedRect,
    WindowInfo,
    MatchMethod,
    ClickButton
)


def test_bounding_box_normalized_conversion():
    bbox = BoundingBox(x=192, y=108, width=384, height=216)
    norm = bbox.to_normalized(container_w=1920, container_h=1080)

    assert norm.x == pytest.approx(0.10, abs=1e-3)
    assert norm.y == pytest.approx(0.10, abs=1e-3)
    assert norm.width == pytest.approx(0.20, abs=1e-3)
    assert norm.height == pytest.approx(0.20, abs=1e-3)
    assert norm.center == (0.20, 0.20)


def test_normalized_rect_pixel_conversion():
    norm = NormalizedRect(x=0.25, y=0.50, width=0.50, height=0.25)
    bbox = norm.to_pixel(container_w=800, container_h=600)

    assert bbox.x == 200
    assert bbox.y == 300
    assert bbox.width == 400
    assert bbox.height == 150
    assert bbox.center == (400, 375)


def test_zero_dimensions_safety():
    bbox = BoundingBox(x=10, y=10, width=20, height=20)
    norm = bbox.to_normalized(container_w=0, container_h=0)
    assert norm.x == 0.0
    assert norm.y == 0.0


def test_window_info_dictionary():
    win = WindowInfo(
        hwnd=12345,
        title="Test Game",
        process_name="game.exe",
        pid=999,
        x=100,
        y=100,
        width=1280,
        height=720,
        client_x=105,
        client_y=130,
        client_width=1270,
        client_height=685,
        is_minimized=False,
        is_focused=True,
    )
    d = win.to_dict()
    assert d["hwnd"] == 12345
    assert d["title"] == "Test Game"
    assert d["client_width"] == 1270
    assert d["is_focused"] is True
