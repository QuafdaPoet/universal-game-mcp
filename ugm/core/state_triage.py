"""Screen-stability triage. Evidence is temporary and implies no action."""
from dataclasses import dataclass
import math
import unicodedata

import cv2
import numpy as np

from .types import OCRItem

MIN_SCORE = .8
MAX_ITEMS = 512
MAX_ANCHORS = 6
MAX_DRIFT = .05
MAX_VISUAL_DELTA = 8.0


@dataclass(frozen=True)
class Snapshot:
    thumbnail: np.ndarray
    anchors: dict[str, tuple[float, float]]


def summarize(frame: np.ndarray, items: list[OCRItem]) -> Snapshot:
    """Save only a small grayscale thumbnail and potential stable anchors."""
    thumbnail = cv2.cvtColor(cv2.resize(frame, (64, 36), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
    if len(items) > MAX_ITEMS:
        # Truncation could hide a duplicate target.
        return Snapshot(thumbnail, {})
    anchors = {}
    seen = set()
    ambiguous = set()
    for item in items:
        if len(item.text) > 72:
            continue
        label = " ".join(unicodedata.normalize("NFKC", item.text).casefold().split())
        # Dynamic numeric controls and counters are poor state anchors.
        if (not 2 <= len(label) <= 72 or sum(ch.isalpha() for ch in label) < 2
                or any(ch.isnumeric() or unicodedata.category(ch).startswith("C") for ch in label)):
            continue
        box = item.bbox_normalized
        coords = (box.x, box.y, box.width, box.height)
        if (not all(map(math.isfinite, coords))
                or not (0 <= box.x < box.x + box.width <= 1 + 1e-12
                        and 0 <= box.y < box.y + box.height <= 1 + 1e-12)
                or not 0 <= item.confidence <= 1):
            continue
        if label in seen:
            ambiguous.add(label)
        seen.add(label)
        if item.confidence >= MIN_SCORE:
            anchors[label] = box.center
    return Snapshot(thumbnail, {key: p for key, p in anchors.items() if key not in ambiguous})


def compare(first: Snapshot, second: Snapshot) -> tuple[list[str], float]:
    """Two readings must agree visually and on at least two unique OCR anchors."""
    delta = float(cv2.absdiff(first.thumbnail, second.thumbnail).mean())
    if delta > MAX_VISUAL_DELTA:
        return [], delta
    shared = sorted(
        label for label, older in first.anchors.items()
        if label in second.anchors and math.dist(older, second.anchors[label]) <= MAX_DRIFT
    )
    return (shared[:MAX_ANCHORS] if len(shared) >= 2 else []), delta
