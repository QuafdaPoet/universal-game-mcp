"""
Unit tests for UGM action verifier (visual diffing, selection verification).
"""

import cv2
import numpy as np
import pytest

from ugm.core.verifier import ActionVerifier
from ugm.core.capture import CaptureManager, FixtureBackend
from ugm.core.ocr import RapidOCREngine
from ugm.core.input import InputController
from ugm.core.window import WindowManager
from ugm.core.types import WindowInfo, NormalizedRect


def test_visual_diff_verification():
    frame1 = np.zeros((200, 200, 3), dtype=np.uint8)
    frame2 = frame1.copy()

    # Create a small changed patch
    cv2.rectangle(frame2, (50, 50), (100, 100), (255, 255, 255), -1)

    wm = WindowManager()
    cap = CaptureManager(backend_override=FixtureBackend(frame1))
    ocr = RapidOCREngine(use_cls=False)
    inp = InputController(wm, dry_run_default=True)

    verifier = ActionVerifier(cap, ocr, inp)

    res = verifier.verify_selection(frame1, frame2)
    assert res.verified is True
    assert res.diff_ratio > 0.05


def test_visual_diff_no_change_fails():
    frame1 = np.ones((200, 200, 3), dtype=np.uint8) * 100
    frame2 = frame1.copy()

    wm = WindowManager()
    cap = CaptureManager(backend_override=FixtureBackend(frame1))
    ocr = RapidOCREngine(use_cls=False)
    inp = InputController(wm, dry_run_default=True)

    verifier = ActionVerifier(cap, ocr, inp)

    res = verifier.verify_selection(frame1, frame2, min_diff_ratio=0.01)
    assert res.verified is False
    assert res.diff_ratio == 0.0
