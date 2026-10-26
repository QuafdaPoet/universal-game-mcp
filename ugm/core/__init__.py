"""
Universal Game MCP (UGM) - Core Subsystems
"""

from .types import (
    BoundingBox,
    NormalizedRect,
    WindowInfo,
    OCRItem,
    OCRResult,
    DetectionItem,
    DetectionResult,
    HoverResult,
    ClickResult,
    ActionResult,
    VerificationResult,
)
from .safety import safety_manager, EmergencyStopTriggered, SafetyViolationError
from .window import WindowManager
from .capture import CaptureManager, FixtureBackend, BetterCamBackend
from .ocr import RapidOCREngine, BaseOCREngine
from .icon import IconDetector
from .input import InputController
from .verifier import ActionVerifier
from .dataset import DatasetCollector

__all__ = [
    "BoundingBox",
    "NormalizedRect",
    "WindowInfo",
    "OCRItem",
    "OCRResult",
    "DetectionItem",
    "DetectionResult",
    "HoverResult",
    "ClickResult",
    "ActionResult",
    "VerificationResult",
    "safety_manager",
    "EmergencyStopTriggered",
    "SafetyViolationError",
    "WindowManager",
    "CaptureManager",
    "FixtureBackend",
    "BetterCamBackend",
    "RapidOCREngine",
    "BaseOCREngine",
    "IconDetector",
    "InputController",
    "ActionVerifier",
    "DatasetCollector",
]
