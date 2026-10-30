"""
Unit tests for UGM capture manager and fixture backend.
"""

import os
import cv2
import numpy as np
import pytest

from ugm.core.capture import CaptureManager, FixtureBackend
from ugm.core.types import NormalizedRect, WindowInfo


def test_fixture_backend_load_and_capture():
    fixture_path = "tests/fixtures/hoi4_topbar.png"
    assert os.path.exists(fixture_path), "Fixture must exist"

    backend = FixtureBackend()
    backend.load_from_file(fixture_path)

    frame = backend.capture()
    assert frame.shape == (1080, 1920, 3)

    # Test ROI cropping
    roi = NormalizedRect(x=0.0, y=0.0, width=0.5, height=0.1)
    crop = backend.capture(roi=roi)
    assert crop.shape[0] == 108
    assert crop.shape[1] == 960


def test_capture_manager_base64_and_save(tmp_path):
    backend = FixtureBackend()
    backend.load_from_file("tests/fixtures/hoi4_topbar.png")

    manager = CaptureManager(backend_override=backend, capture_dir=str(tmp_path))
    frame = manager.capture()

    assert frame is not None
    assert manager.last_latency_ms > 0

    # Test Base64 encoding
    b64 = manager.encode_base64_png(frame)
    assert isinstance(b64, str)
    assert len(b64) > 1000

    # Test saving image
    out_file = str(tmp_path / "test_out.png")
    saved_path = manager.save_image(frame, out_file)
    assert os.path.exists(saved_path)
