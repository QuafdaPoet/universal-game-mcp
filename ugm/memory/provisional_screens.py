"""Bounded observation-only screen identities, never graph state names."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
import json
import math
import re
import sqlite3
import time
from uuid import uuid4

import cv2
import numpy as np

from ..core.state_triage import MAX_DRIFT, MAX_VISUAL_DELTA, Snapshot
from .database import DatabaseManager

MAX_SCREENS = 64
MAX_RECEIPTS = 24
MAX_ANCHORS = 32
MIN_ANCHORS = 3
RECEIPT_AGE_S = 30 * 86400


@dataclass(frozen=True)
class ScreenSignature:
    anchors: dict[str, tuple[float, float]]
    thumbnail: np.ndarray


def fingerprint(first: Snapshot, second: Snapshot) -> ScreenSignature | None:
    """Two same-scope acquisitions must agree before an anonymous ID is possible."""
    if (not MIN_ANCHORS <= len(second.anchors) <= MAX_ANCHORS
            or first.anchors.keys() != second.anchors.keys()
            or cv2.absdiff(first.thumbnail, second.thumbnail).mean() > MAX_VISUAL_DELTA
            or any(math.dist(pos, second.anchors[text]) > MAX_DRIFT for text, pos in first.anchors.items())):
        return None
    hashed = {sha256(text.encode("utf-8")).hexdigest(): pos for text, pos in second.anchors.items()}
    return ScreenSignature(hashed, second.thumbnail.copy())


def compare_signature(observed: ScreenSignature, seen: ScreenSignature) -> str:
    delta = float(cv2.absdiff(observed.thumbnail, seen.thumbnail).mean())
    common = observed.anchors.keys() & seen.anchors.keys()
    if (observed.anchors.keys() == seen.anchors.keys() and delta <= 6.0
            and all(math.dist(observed.anchors[k], seen.anchors[k]) <= .045 for k in common)):
        return "match"
    # Treat nearby signatures as a conflict, not a second identity or the closest match.
    if delta <= 3.0 or (delta <= 20.0 and len(common) >= 2):
        return "conflict"
    return "different"


@dataclass(frozen=True)
class ScreenObservation:
    status: str
    provisional_id: str | None = None
    receipt_count: int = 0


class ProvisionalScreenStore:
    def __init__(self, db: DatabaseManager):
        self.db = db

    @staticmethod
    def _match(conn: sqlite3.Connection, game: str, signature: ScreenSignature) -> tuple[str, str | None, int]:
        rows = conn.execute("""
            SELECT screen_id, anchors, thumbnail
            FROM provisional_screens WHERE game_id = ? LIMIT ?
        """, (game, MAX_SCREENS + 1)).fetchall()
        if len(rows) > MAX_SCREENS:
            return "capacity_reached", None, len(rows)
        ids = []
        conflict = False
        for row in rows:
            seen = ScreenSignature(json.loads(row["anchors"]), np.frombuffer(row["thumbnail"], np.uint8).reshape(36, 64))
            relation = compare_signature(signature, seen)
            if relation == "match":
                ids.append(row["screen_id"])
            elif relation == "conflict":
                conflict = True
        if conflict or len(ids) > 1:
            return "ambiguous", None, len(rows)
        if ids:
            return "match", ids[0], len(rows)
        return "unseen", None, len(rows)

    @staticmethod
    def _summary(conn: sqlite3.Connection, game: str, identity: str, now: float) -> ScreenObservation:
        count, attachments = conn.execute("""
            SELECT count(*), count(DISTINCT attachment_token)
            FROM provisional_screen_receipts
            WHERE game_id = ? AND screen_id = ? AND recorded_at BETWEEN ? AND ?
        """, (game, identity, now - RECEIPT_AGE_S, now)).fetchone()
        status = "provisional_match" if count >= 3 and attachments >= 2 else "provisional_candidate"
        return ScreenObservation(status, identity, count)

    def observe(
        self, game: str, signature: ScreenSignature, *,
        receipt: tuple[str, str] | None = None,
        validate_before_commit: Callable[[], None] | None = None,
    ) -> ScreenObservation:
        if self.db.simulation:
            raise ValueError("Provisional screen evidence requires native observation storage.")
        if receipt is not None and (not re.fullmatch(r"[a-f0-9]{64}", receipt[0])
                                    or not re.fullmatch(r"[a-f0-9]{32}", receipt[1])):
            raise ValueError("Invalid native screen receipt.")
        conn = self.db.get_connection()
        now = time.time()
        if receipt is None:
            status, identity, _ = self._match(conn, game, signature)
            return self._summary(conn, game, identity, now) if identity else ScreenObservation(status)
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            status, identity, existing = self._match(conn, game, signature)
            if status in {"ambiguous", "capacity_reached"}:
                return ScreenObservation(status)
            capture_digest, attachment_token = receipt
            used = conn.execute("""
                SELECT screen_id FROM provisional_screen_receipts WHERE game_id = ? AND capture_sha256 = ?
            """, (game, capture_digest)).fetchone()
            if used:
                return (self._summary(conn, game, identity, now) if identity == used["screen_id"]
                        else ScreenObservation("ambiguous"))
            if identity is None:
                if existing >= MAX_SCREENS:
                    return ScreenObservation("capacity_reached")
                identity = "provisional_" + uuid4().hex
                conn.execute("INSERT INTO provisional_screens VALUES (?, ?, ?, ?)", (
                    game, identity, json.dumps(signature.anchors, sort_keys=True), signature.thumbnail.tobytes(),
                ))
            else:
                count = conn.execute("""
                    SELECT count(*) FROM provisional_screen_receipts WHERE game_id = ? AND screen_id = ?
                """, (game, identity)).fetchone()[0]
                if count >= MAX_RECEIPTS:
                    return self._summary(conn, game, identity, now)
            conn.execute("INSERT INTO provisional_screen_receipts VALUES (?, ?, ?, ?, ?)",
                         (game, identity, capture_digest, attachment_token, now))
            if validate_before_commit is not None:
                validate_before_commit()
            return self._summary(conn, game, identity, now)
