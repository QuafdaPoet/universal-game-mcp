"""Bounded, read-only grounding of an agent-supplied goal in visible UI labels."""

from __future__ import annotations

import math
import re
import unicodedata
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .explorer import UIElementCandidate

MAX_GOAL_CHARS = 160
MAX_GOAL_TERMS = 20
MAX_RESULTS = 12
MAX_LABEL_CHARS = 160


def _tokens(value: str) -> set[str]:
    return set(re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", value).casefold()))


def validate_goal(goal: str) -> set[str]:
    if len(goal) > MAX_GOAL_CHARS:
        raise ValueError(f"Exploration goal must contain at most {MAX_GOAL_CHARS} characters.")
    terms = _tokens(goal)
    if not 1 <= len(terms) <= MAX_GOAL_TERMS:
        raise ValueError(f"Exploration goal must contain 1–{MAX_GOAL_TERMS} distinct words or numbers.")
    return terms


def grounded_matches(
    candidates: list[UIElementCandidate], terms: set[str], limit: int, source: str,
) -> tuple[int, list[dict]]:
    """Rank OCR overlap; shape-only evidence and speculative actions are excluded."""
    ranked = []
    for candidate in candidates:
        if candidate.details.get("source") != "ocr" or not candidate.text:
            continue
        label = candidate.text[:MAX_LABEL_CHARS]
        overlap = terms & _tokens(label)
        box = candidate.bbox
        coords = (box.x, box.y, box.width, box.height)
        if (not overlap or not all(map(math.isfinite, coords))
                or not (0 <= box.x < box.x + box.width <= 1 + 1e-12
                        and 0 <= box.y < box.y + box.height <= 1 + 1e-12)):
            continue
        score = candidate.details.get("raw_score", 0.0)
        if not isinstance(score, (float, int)) or not math.isfinite(score) or not 0 <= score <= 1:
            continue
        ranked.append((len(overlap), float(score), coords, candidate.candidate_id, label, sorted(overlap)))
    ranked.sort(key=lambda row: (-row[0], -row[1], row[2], row[3]))
    matches = [{
        "observation_id": candidate_id[:96],
        "label": label,
        "matched_terms": matched,
        "ocr_confidence": round(score, 3),
        "bbox_normalized": dict(zip(("x", "y", "width", "height"), coords)),
        "observation_source": source,
        "interactivity_verified": False,
    } for _, score, coords, candidate_id, label, matched in ranked[:limit]]
    return len(ranked), matches
