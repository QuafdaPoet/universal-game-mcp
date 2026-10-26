"""
Universal Game MCP (UGM) - HOI4 Computer Vision & Perception Algorithms.
Selectively adapted from HOI4 Live Tools (Apache-2.0, Copyright 2026 Klim Pashkov):
- Morphological differential tooltip box detection
- Event and news popup window segmentation
- High-accuracy date parsing with OCR digit substitution
- Hazard-stripe pause state detection and speed bar segmentation
"""

from __future__ import annotations
import re
import datetime
import logging
from typing import Dict, List, Optional, Tuple, Any
import numpy as np
import cv2

from ...core.types import NormalizedRect, OCRResult
from ...core.ocr import BaseOCREngine

logger = logging.getLogger("ugm.adapter.hoi4.perception")

MONTHS = {
    m: i + 1 for i, m in enumerate([
        "jan", "feb", "mar", "apr", "may", "jun",
        "jul", "aug", "sep", "oct", "nov", "dec"
    ])
}
DATE_RX = re.compile(r"(\d{1,2})\s*[:.;]\s*(\d{2})\s*[,.]?\s*(\d{1,2})\s+([A-Za-z]{3})[A-Za-z]*\s*[,.]?\s*(\d{4})")
_DIGIT_FIX = str.maketrans({
    "O": "0", "o": "0", "D": "0", "l": "1", "I": "1", "|": "1", "i": "1",
    "S": "5", "s": "5", "B": "8", "a": "4", "Z": "2", "z": "2", "g": "9", "q": "9"
})


def parse_hoi4_date(text: str, lenient: bool = True) -> Optional[Dict[str, Any]]:
    """
    Parses '12:00, 1 Jan, 1936' into structured dict.
    Applies OCR confusion substitution for resilient digit extraction.
    """
    if not text:
        return None
    t = text.replace("\n", " ").strip()
    m = DATE_RX.search(t)
    if not m:
        # Candidate fix: replace common OCR letters in date-like patterns
        fixed = re.sub(
            r"([0-9OoIlZzaA|]{1,2})\s*[:.;]\s*([0-9Oo|]{2})",
            lambda x: x.group(1).translate(_DIGIT_FIX) + ":" + x.group(2).translate(_DIGIT_FIX),
            t
        )
        fixed = re.sub(
            r"([0-9OoIlZzaA|]{1,2})\s+([A-Za-z]{3})",
            lambda x: x.group(1).translate(_DIGIT_FIX) + " " + x.group(2),
            fixed
        )
        fixed = re.sub(
            r"([0-9OoIlZzaA|]{4})",
            lambda x: x.group(1).translate(_DIGIT_FIX),
            fixed
        )
        m = DATE_RX.search(fixed)
        if not m:
            return None

    hour_str, minute_str, day_str, mon_str, year_str = m.groups()
    mon = MONTHS.get(mon_str[:3].lower())
    try:
        hour, day, year = int(hour_str), int(day_str), int(year_str)
    except ValueError:
        return None

    uncertain = False
    if not 0 <= hour <= 24:
        if not lenient:
            return None
        hour, uncertain = 0, True
    if mon is None or not (1 <= day <= 31 and 1800 <= year <= 2200):
        return None

    try:
        datetime.date(year, mon, day)
    except ValueError:
        return None

    out = {
        "year": year,
        "month": mon,
        "day": day,
        "hour": hour,
        "text": m.group(0),
        "iso": f"{year:04d}-{mon:02d}-{day:02d}T{hour:02d}:00",
        "hoi4": f"{year}.{mon}.{day}.{hour}"
    }
    if uncertain:
        out["hour_uncertain"] = True
    return out


def detect_pause_state(img: np.ndarray, scale: float = 1.0) -> Tuple[str, Dict[str, Any]]:
    """
    Determines ('paused' | 'running' | 'unknown') from the speed icon color
    and date box hazard-stripe pixels.
    """
    h, w = img.shape[:2]
    # Speed icon region
    x0, x1 = int(w - 295 * scale), int(w - 277 * scale)
    y0, y1 = int(11 * scale), int(30 * scale)
    if x0 < 0 or y0 < 0 or x1 > w or y1 > h:
        return "unknown", {"reason": "frame_too_small"}

    a = img[y0:y1, x0:x1].astype(np.int16)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    green = int(((g > 120) & (g > r + 30) & (g > b + 30)).sum())
    warm = int(((r > 70) & (r - b > 18) & (r >= g) & (g - b > 5)).sum())

    # Hazard stripe check in paused date box
    sx0, sx1 = int(w - 270 * scale), int(w - 140 * scale)
    sy0, sy1 = int(9 * scale), int(31 * scale)
    sa = img[sy0:sy1, sx0:sx1].astype(np.int16)
    sr, sg, sb = sa[..., 0], sa[..., 1], sa[..., 2]
    smx = sa.max(2)
    stripe = (sr - sb > 14) & (sg - sb > 8) & (smx > 35) & (smx < 200)
    stripe_score = float(stripe.mean())

    if green > 12 and green > warm:
        state = "running"
    elif warm > 10 or stripe_score > 0.12:
        state = "paused"
    else:
        state = "unknown"

    return state, {
        "green_pixels": green,
        "warm_pixels": warm,
        "stripe_score": round(stripe_score, 3)
    }


def detect_speed_level(img: np.ndarray, scale: float = 1.0) -> Optional[int]:
    """
    Detects lit segments (1-5) of the five-step speed bar below the date widget.
    """
    h, w = img.shape[:2]
    y0, y1 = int(35 * scale), int(40 * scale)
    x0, x1 = int(w - 282 * scale), int(w - 157 * scale)
    if x0 < 0 or y0 < 0 or x1 > w or y1 > h:
        return None

    a = img[y0:y1, x0:x1].astype(np.int16)
    if a.size == 0:
        return None
    seg = (x1 - x0) / 5.0
    lit = 0
    for i in range(5):
        s = a[:, int(i * seg) + 3:int((i + 1) * seg) - 3].reshape(-1, 3).mean(0)
        if s.max() > 90 and s.max() - s.min() > 25:
            lit += 1
    return lit if lit > 0 else None


def detect_tooltip_box(
    before_frame: np.ndarray,
    after_frame: np.ndarray,
    cursor_xy: Tuple[int, int],
    max_dist: int = 700,
    min_area: int = 1200
) -> Optional[List[int]]:
    """
    Finds [x, y, w, h] of tooltip window that appeared between two frames.
    Uses morphological closing on differential pixel delta.
    """
    if before_frame is None or after_frame is None or before_frame.shape != after_frame.shape:
        return None

    # Absolute difference on all channels
    d = (np.abs(after_frame.astype(np.int16) - before_frame.astype(np.int16)).max(2) > 22).astype(np.uint8)
    H, W = d.shape
    cx, cy = cursor_xy

    # Mask out top-right date box hazard stripes if not hovering there
    if not (cx > W - 470 and cy < 50):
        d[0:48, max(0, W - 470):max(0, W - 60)] = 0

    # Morphological closing
    kernel = np.ones((11, 11), np.uint8)
    d = cv2.morphologyEx(d, cv2.MORPH_CLOSE, kernel)

    n, labels, stats, _ = cv2.connectedComponentsWithStats(d, connectivity=8)
    best = None
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if area < min_area or w < 50 or h < 16:
            continue
        fill = area / float(w * h)
        if fill < 0.55:
            continue

        dx = max(x - cx, 0, cx - (x + w))
        dy = max(y - cy, 0, cy - (y + h))
        if (dx * dx + dy * dy) ** 0.5 > max_dist:
            continue

        score = area * fill
        if best is None or score > best[0]:
            best = (score, [int(x), int(y), int(w), int(h)])

    return best[1] if best else None


def detect_popups(frame: np.ndarray) -> List[Dict[str, Any]]:
    """
    Detects modal event popups (country events on warm parchment, news events on grey paper)
    and their option buttons. Resolution-independent.
    """
    if frame is None or len(frame.shape) < 3:
        return []

    H, W = frame.shape[:2]
    # Downsample for fast paper segmentation
    small = cv2.resize(frame, (W // 2, H // 2))
    r = small[..., 0].astype(np.int16)
    g = small[..., 1].astype(np.int16)
    b = small[..., 2].astype(np.int16)

    # Warm parchment & grey paper masks
    warm = (r > 160) & (g > 130) & (b > 80) & (r - b > 42) & (r - b < 118) & (r >= g) & (g >= b) & (g - b > 18)
    grey = (r > 145) & (g > 135) & (b > 120) & (r - b >= 3) & (r - b < 42) & (r >= g - 3) & (g >= b - 3) & (r < 242)

    popups: List[Dict[str, Any]] = []
    for kind, mask in [("event", warm), ("news", grey)]:
        closed = cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
        n, labels, stats, _ = cv2.connectedComponentsWithStats(closed, connectivity=8)
        for i in range(1, n):
            x, y, w, h, area = stats[i]
            # Must be substantial fraction of screen (at least 15% width, 20% height)
            if w < (W // 2) * 0.15 or h < (H // 2) * 0.20:
                continue
            fill = area / float(w * h)
            if fill < 0.60:
                continue

            # Scale back to full image client coordinates
            fx, fy, fw, fh = int(x * 2), int(y * 2), int(w * 2), int(h * 2)
            popups.append({
                "kind": kind,
                "box": [fx, fy, fw, fh],
                "normalized_rect": {
                    "x": round(fx / W, 4),
                    "y": round(fy / H, 4),
                    "width": round(fw / W, 4),
                    "height": round(fh / H, 4)
                },
                "fill_ratio": round(fill, 3)
            })

    return popups
