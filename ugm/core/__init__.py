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
    TargetStatus,
    TargetCandidate,
    TargetResolution,
    VisionAgentEscalation,
)
from .safety import safety_manager, EmergencyStopTriggered, SafetyViolationError
from .window import WindowManager
from .capture import CaptureManager, FixtureBackend, BetterCamBackend
from .ocr import RapidOCREngine, RapidOCRLiteEngine, BaseOCREngine, get_ocr_engine
from .icon import IconDetector
from .input import InputController
from .verifier import ActionVerifier
from .dataset import DatasetCollector
from .target import TargetResolver, TargetExecutor

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
    "TargetStatus",
    "TargetCandidate",
    "TargetResolution",
    "VisionAgentEscalation",
    "safety_manager",
    "EmergencyStopTriggered",
    "SafetyViolationError",
    "WindowManager",
    "CaptureManager",
    "FixtureBackend",
    "BetterCamBackend",
    "RapidOCREngine",
    "RapidOCRLiteEngine",
    "BaseOCREngine",
    "get_ocr_engine",
    "IconDetector",
    "InputController",
    "ActionVerifier",
    "DatasetCollector",
    "TargetResolver",
    "TargetExecutor",
]
