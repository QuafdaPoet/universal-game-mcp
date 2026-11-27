"""Durable, game-scoped route suppression, independent of snapshot state."""
from __future__ import annotations

import json
import sqlite3
from typing import Any


def transition_identity(from_state: str, to_state: str, action_type: str,
                        params: dict[str, Any]) -> tuple[str, str, str, str]:
    """Parameter map ordering is irrelevant; action and values still match exactly."""
    if not all(isinstance(v, str) and v.strip() for v in (from_state, to_state, action_type)):
        raise ValueError("Transition identity requires non-empty from_state, to_state, and action_type.")
    if not isinstance(params, dict):
        raise ValueError("Transition identity params must be an object.")
    return from_state, to_state, action_type, json.dumps(params, sort_keys=True, allow_nan=False)


def quarantine_transition(conn: sqlite3.Connection, game_id: str,
                          identity: tuple[str, str, str, str],
                          timestamp: float, reason: str) -> None:
    # The original quarantine survives repeated rejection, status changes and rollback.
    conn.execute("""
        INSERT INTO transition_quarantine
            (game_id, from_state, to_state, action_type, params, rejected_at, reason)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(game_id, from_state, to_state, action_type, params) DO NOTHING
    """, (game_id, *identity, timestamp, reason))


def get_quarantined_transitions(conn: sqlite3.Connection, game_id: str,
                                from_state: str | None = None) -> set[tuple[str, str, str, str]]:
    query = "SELECT from_state, to_state, action_type, params FROM transition_quarantine WHERE game_id = ?"
    params = (game_id,)
    if from_state is not None:
        query += " AND from_state = ?"
        params += (from_state,)
    return {tuple(row) for row in conn.execute(query, params)}
