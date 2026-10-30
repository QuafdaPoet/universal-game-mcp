"""
Universal Game MCP (UGM) - Core Data Types & Models
Defines typed models for windows, coordinates, OCR, icon detection, inputs, and verifications.
"""

from __future__ import annotations
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


class MatchMethod(str, Enum):
    TEMPLATE_CCOEFF_NORMED = "cv2.TM_CCOEFF_NORMED"
    TEMPLATE_CCORR_NORMED = "cv2.TM_CCORR_NORMED"
    TEMPLATE_SQDIFF_NORMED = "cv2.TM_SQDIFF_NORMED"


class ClickButton(str, Enum):
    LEFT = "left"
    RIGHT = "right"
    MIDDLE = "middle"


@dataclass
class BoundingBox:
    """Bounding box in pixel coordinates."""
    x: int
    y: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height

    @property
    def center(self) -> Tuple[int, int]:
        return (self.x + self.width // 2, self.y + self.height // 2)

    def to_normalized(self, container_w: int, container_h: int) -> NormalizedRect:
        if container_w <= 0 or container_h <= 0:
            return NormalizedRect(0.0, 0.0, 0.0, 0.0)
        return NormalizedRect(
            x=round(self.x / container_w, 4),
            y=round(self.y / container_h, 4),
            width=round(self.width / container_w, 4),
            height=round(self.height / container_h, 4),
        )


@dataclass
class NormalizedRect:
    """Normalized bounding box relative to game window [0.0, 1.0]."""
    x: float
    y: float
    width: float
    height: float

    @property
    def right(self) -> float:
        return min(1.0, round(self.x + self.width, 4))

    @property
    def bottom(self) -> float:
        return min(1.0, round(self.y + self.height, 4))

    @property
    def center(self) -> Tuple[float, float]:
        return (round(self.x + self.width / 2.0, 4), round(self.y + self.height / 2.0, 4))

    def to_pixel(self, container_w: int, container_h: int) -> BoundingBox:
        x = max(0, int(round(self.x * container_w)))
        y = max(0, int(round(self.y * container_h)))
        w = max(1, int(round(self.width * container_w)))
        h = max(1, int(round(self.height * container_h)))
        # clamp
        if x + w > container_w:
            w = max(1, container_w - x)
        if y + h > container_h:
            h = max(1, container_h - y)
        return BoundingBox(x=x, y=y, width=w, height=h)


@dataclass
class WindowInfo:
    """Information about target game window."""
    hwnd: int
    title: str
    process_name: str
    pid: int
    x: int
    y: int
    width: int
    height: int
    client_x: int
    client_y: int
    client_width: int
    client_height: int
    is_minimized: bool
    is_focused: bool
    dpi_scale: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class OCRItem:
    """A detected text segment with coordinates and confidence."""
    text: str
    confidence: float
    bbox_pixel: BoundingBox
    bbox_normalized: NormalizedRect
    polygon: List[List[int]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "confidence": round(self.confidence, 4),
            "bbox_pixel": asdict(self.bbox_pixel),
            "bbox_normalized": asdict(self.bbox_normalized),
            "polygon": self.polygon,
        }


@dataclass
class OCRResult:
    """Result of an event-based OCR query."""
    items: List[OCRItem]
    full_text: str
    engine: str
    latency_ms: float
    roi_normalized: Optional[NormalizedRect] = None
    event_tag: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "items": [item.to_dict() for item in self.items],
            "full_text": self.full_text,
            "engine": self.engine,
            "latency_ms": round(self.latency_ms, 2),
            "roi_normalized": asdict(self.roi_normalized) if self.roi_normalized else None,
            "event_tag": self.event_tag,
            "count": len(self.items),
        }


@dataclass
class DetectionItem:
    """Detected icon match with bounding box and confidence score."""
    icon_name: str
    confidence: float
    bbox_pixel: BoundingBox
    bbox_normalized: NormalizedRect
    center_pixel: Tuple[int, int]
    center_normalized: Tuple[float, float]
    match_method: str = MatchMethod.TEMPLATE_CCOEFF_NORMED.value

    def to_dict(self) -> dict[str, Any]:
        return {
            "icon_name": self.icon_name,
            "confidence": round(self.confidence, 4),
            "bbox_pixel": asdict(self.bbox_pixel),
            "bbox_normalized": asdict(self.bbox_normalized),
            "center_pixel": self.center_pixel,
            "center_normalized": self.center_normalized,
            "match_method": self.match_method,
        }


@dataclass
class VisionAgentEscalation:
    """Escalation payload when local perception encounters ambiguity or uncertainty."""
    needed: bool
    reason: str
    target_type: str = "ui_element"
    crop_base64: Optional[str] = None
    candidates: List[Dict[str, Any]] = field(default_factory=list)
    suggested_prompt: str = ""
    roi_normalized: Optional[NormalizedRect] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "needed": self.needed,
            "reason": self.reason,
            "target_type": self.target_type,
            "crop_base64": self.crop_base64,
            "candidates": self.candidates,
            "suggested_prompt": self.suggested_prompt,
            "roi_normalized": asdict(self.roi_normalized) if self.roi_normalized else None,
        }


class TargetStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    AMBIGUOUS = "ambiguous"
    NOT_FOUND = "not_found"


@dataclass
class DetectionResult:
    """Result of an icon detection query."""
    items: List[DetectionItem]
    latency_ms: float
    is_ambiguous: bool
    threshold: float
    roi_normalized: Optional[NormalizedRect] = None
    status: str = "found"
    escalation: Optional[Dict[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "items": [item.to_dict() for item in self.items],
            "latency_ms": round(self.latency_ms, 2),
            "is_ambiguous": self.is_ambiguous,
            "threshold": self.threshold,
            "count": len(self.items),
            "roi_normalized": asdict(self.roi_normalized) if self.roi_normalized else None,
            "status": self.status,
            "escalation": self.escalation,
        }


@dataclass
class HoverResult:
    """Result of hovering target and reading tooltip."""
    target_normalized: Tuple[float, float]
    target_pixel: Tuple[int, int]
    duration_ms: int
    tooltip_detected: bool
    tooltip_text: Optional[str]
    tooltip_bbox: Optional[NormalizedRect]
    latency_ms: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_normalized": self.target_normalized,
            "target_pixel": self.target_pixel,
            "duration_ms": self.duration_ms,
            "tooltip_detected": self.tooltip_detected,
            "tooltip_text": self.tooltip_text,
            "tooltip_bbox": asdict(self.tooltip_bbox) if self.tooltip_bbox else None,
            "latency_ms": round(self.latency_ms, 2),
        }


@dataclass
class ClickResult:
    """Result of a safe click action."""
    x_normalized: float
    y_normalized: float
    x_pixel: int
    y_pixel: int
    button: str
    clicks: int
    dry_run: bool
    executed: bool
    verification_passed: Optional[bool]
    reason: str
    latency_ms: float
    status: str = "success"  # "success", "failed", "ambiguous", "not_found"
    evidence: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "x_normalized": self.x_normalized,
            "y_normalized": self.y_normalized,
            "x_pixel": self.x_pixel,
            "y_pixel": self.y_pixel,
            "button": self.button,
            "clicks": self.clicks,
            "dry_run": self.dry_run,
            "executed": self.executed,
            "verification_passed": self.verification_passed,
            "reason": self.reason,
            "latency_ms": round(self.latency_ms, 2),
            "status": self.status,
            "evidence": self.evidence,
        }


@dataclass
class TargetCandidate:
    """A visual or OCR candidate for target resolution."""
    candidate_id: str
    candidate_type: str  # "icon", "text", "composite"
    confidence: float
    bbox_pixel: BoundingBox
    bbox_normalized: NormalizedRect
    center_normalized: Tuple[float, float]
    text: Optional[str] = None
    tooltip_text: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "candidate_type": self.candidate_type,
            "confidence": round(self.confidence, 4),
            "bbox_pixel": asdict(self.bbox_pixel),
            "bbox_normalized": asdict(self.bbox_normalized),
            "center_normalized": self.center_normalized,
            "text": self.text,
            "tooltip_text": self.tooltip_text,
            "details": self.details,
        }


@dataclass
class TargetResolution:
    """Result of resolving a target query against screen visual state."""
    status: str  # "found", "ambiguous", "not_found", "rejected"
    selector_id: Optional[str] = None
    target_candidate: Optional[TargetCandidate] = None
    all_candidates: List[TargetCandidate] = field(default_factory=list)
    coordinates_normalized: Optional[Tuple[float, float]] = None
    evidence: Dict[str, Any] = field(default_factory=dict)
    escalation: Optional[VisionAgentEscalation] = None
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "selector_id": self.selector_id,
            "target": self.target_candidate.to_dict() if self.target_candidate else None,
            "candidates_count": len(self.all_candidates),
            "candidates": [c.to_dict() for c in self.all_candidates],
            "coordinates_normalized": self.coordinates_normalized,
            "evidence": self.evidence,
            "escalation": self.escalation.to_dict() if self.escalation else None,
            "reason": self.reason,
        }


@dataclass
class ActionResult:
    """General action result for keystrokes or system commands."""
    success: bool
    action: str
    message: str
    dry_run: bool
    latency_ms: float
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "action": self.action,
            "message": self.message,
            "dry_run": self.dry_run,
            "latency_ms": round(self.latency_ms, 2),
            "details": self.details,
        }


@dataclass
class VerificationResult:
    """Result of visual verification comparing states."""
    verified: bool
    diff_ratio: float
    message: str
    latency_ms: float
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "verified": self.verified,
            "diff_ratio": round(self.diff_ratio, 4),
            "message": self.message,
            "latency_ms": round(self.latency_ms, 2),
            "details": self.details,
        }
