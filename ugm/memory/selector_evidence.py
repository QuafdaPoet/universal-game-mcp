"""Internal evidence ledger for OCR label recipes; never dispatches input.

Only a future scoped native capture adapter should issue Receipt objects. This
module intentionally does not accept serialized MCP receipts or goal IDs.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
import math
import re
import sqlite3
import time
import unicodedata

from ..core.types import NormalizedRect, OCRItem
from .database import DatabaseManager

MIN_CONFIDENCE = .80
MIN_POSITIVES = 3
MIN_ATTACHMENTS = 2
MAX_RECEIPTS_PER_RECIPE = 128
EVIDENCE_MAX_AGE_S = 30 * 24 * 3600


def _normalize_label(label: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", label).casefold().split())


def _valid_box(box: NormalizedRect) -> bool:
    coords = (box.x, box.y, box.width, box.height)
    return (all(map(math.isfinite, coords))
            and 0 <= box.x < box.x + box.width <= 1 + 1e-12
            and 0 <= box.y < box.y + box.height <= 1 + 1e-12)


@dataclass(frozen=True)
class LabelMatch:
    status: str
    box: NormalizedRect | None = None


def locate_exact_label(label: str, items: Iterable[OCRItem]) -> LabelMatch:
    """Resolve only a unique exact label, using current OCR and not past geometry."""
    target = _normalize_label(label)
    if not target:
        raise ValueError("A selector label cannot be empty.")
    match: OCRItem | None = None
    readable = False
    for item in items:
        if not math.isfinite(item.confidence) or not 0 <= item.confidence <= 1 or not _valid_box(item.bbox_normalized):
            continue
        text = _normalize_label(item.text)
        readable = readable or (item.confidence >= MIN_CONFIDENCE and bool(text))
        if text == target:
            if match is not None:
                return LabelMatch("ambiguous")
            match = item
    if match is not None:
        if match.confidence >= MIN_CONFIDENCE:
            return LabelMatch("found", replace(match.bbox_normalized))
        return LabelMatch("inconclusive")
    return LabelMatch("not_found" if readable else "inconclusive")


@dataclass(frozen=True)
class SelectorRecipe:
    game_id: str
    state_name: str
    selector_id: str
    label: str

    def validated(self) -> tuple[tuple[str, str, str], str]:
        key = (self.game_id, self.state_name, self.selector_id)
        if any(not value.strip() or len(value) > 128 for value in key):
            raise ValueError("Invalid game, state or selector ID.")
        if self.state_name.strip().casefold() in {"unknown", "ambiguous"}:
            raise ValueError("Selector recipes require a known state.")
        label = _normalize_label(self.label)
        if not label or len(self.label) > 160 or len(label) > 160:
            raise ValueError("Invalid selector label.")
        return key, label


@dataclass(frozen=True)
class Receipt:
    receipt_id: str
    capture_sha256: str
    attachment_token: str
    game_id: str
    state_name: str
    source: str

    def validate(self, recipe: SelectorRecipe) -> None:
        if self.source != "live_observation":
            raise ValueError("Selector evidence requires a live observation.")
        if (self.game_id, self.state_name) != (recipe.game_id, recipe.state_name):
            raise ValueError("Receipt must match the selector's game and state.")
        if (not re.fullmatch(r"[a-f0-9]{32}", self.receipt_id)
                or not re.fullmatch(r"[a-f0-9]{64}", self.capture_sha256)
                or not re.fullmatch(r"[a-f0-9]{32}", self.attachment_token)):
            raise ValueError("Invalid server-issued selector evidence identifiers.")


@dataclass(frozen=True)
class EvidenceStatus:
    status: str
    observations: int = 0
    contradictions: int = 0
    new_receipt: bool = False


class SelectorEvidenceStore:
    def __init__(self, db: DatabaseManager):
        self.db = db

    def status(self, recipe: SelectorRecipe) -> EvidenceStatus:
        key, label = recipe.validated()
        return self._status(self.db.get_connection(), key, label, time.time())

    @staticmethod
    def _status(
        conn: sqlite3.Connection, key: tuple[str, str, str], label: str, now: float,
    ) -> EvidenceStatus:
        rows = conn.execute("""
            SELECT r.normalized_label, t.outcome, t.attachment_token, t.observed_at
            FROM semantic_selector_recipes r
            JOIN semantic_selector_receipts t USING (game_id, state_name, selector_id)
            WHERE (r.game_id, r.state_name, r.selector_id) = (?, ?, ?)
            ORDER BY t.sequence_id
        """, key).fetchall()
        if not rows:
            return EvidenceStatus("unseen")
        if rows[0]["normalized_label"] != label:
            raise ValueError("A selector recipe's label is immutable; use a new selector ID.")
        recent_positives, contradictions, attachments = 0, 0, set()
        for row in rows:
            if row["outcome"] != "found":
                recent_positives = 0
                attachments.clear()
                contradictions += 1
            elif 0 <= now - row["observed_at"] < EVIDENCE_MAX_AGE_S:
                recent_positives += 1
                attachments.add(row["attachment_token"])
        if len(rows) >= MAX_RECEIPTS_PER_RECIPE:
            status = "needs_review"
        elif recent_positives >= MIN_POSITIVES and len(attachments) >= MIN_ATTACHMENTS:
            status = "repeat_observed"
        else:
            status = "contradicted" if contradictions else "candidate"
        return EvidenceStatus(status, len(rows), contradictions)

    def record(
        self, recipe: SelectorRecipe, receipt: Receipt, items: Iterable[OCRItem],
        *, validate_before_commit: Callable[[], None] | None = None,
    ) -> EvidenceStatus:
        if self.db.simulation:
            raise ValueError("Live selector evidence cannot be written to a simulation database.")
        key, label = recipe.validated()
        receipt.validate(recipe)
        result = locate_exact_label(label, items)
        conn = self.db.get_connection()
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            now = time.time()
            current = self._status(conn, key, label, now)
            if result.status == "inconclusive":
                return current
            if conn.execute("""
                SELECT 1 FROM semantic_selector_receipts
                WHERE (game_id, state_name, selector_id) = (?, ?, ?)
                  AND (receipt_id = ? OR capture_sha256 = ?)
            """, (*key, receipt.receipt_id, receipt.capture_sha256)).fetchone():
                return current
            if current.observations >= MAX_RECEIPTS_PER_RECIPE:
                raise ValueError("Selector receipt limit reached; review or version the recipe before adding evidence.")
            conn.execute("""
                INSERT INTO semantic_selector_recipes (game_id, state_name, selector_id, normalized_label)
                VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING
            """, (*key, label))
            conn.execute("""
                INSERT INTO semantic_selector_receipts
                    (game_id, state_name, selector_id, receipt_id, capture_sha256,
                     attachment_token, source, outcome, observed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (*key, receipt.receipt_id, receipt.capture_sha256,
                  receipt.attachment_token, receipt.source, result.status, now))
            updated = replace(self._status(conn, key, label, now), new_receipt=True)
            if validate_before_commit is not None:
                validate_before_commit()
            return updated
