"""Internal, lossless checkpoint representation for per-game knowledge tables."""

from __future__ import annotations

import json
import math
import sqlite3
from typing import Any

# Versioned independently of the SQLite schema. The previously unversioned
# format dropped facts (notably rejected edges and selector provenance).
_FORMAT_VERSION = 1
_TABLES = ("ui_states", "navigation_transitions", "learned_skills", "selectors_memory")
_JSON_COLUMNS = {
    "ui_states": {"evidence": dict, "metadata": dict},
    "navigation_transitions": {"params": dict, "evidence": dict},
    "learned_skills": {"preconditions": dict, "steps": list, "post_verification": dict},
    "selectors_memory": {"selector_data": dict},
}


def _reject_non_finite(value: str) -> None:
    raise ValueError(f"Non-finite JSON number: {value}")


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        _reject_non_finite(value)
    return number


def _unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    obj: dict[str, Any] = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError("Duplicate JSON object key in checkpoint")
        obj[key] = value
    return obj


def _load_json(data: str) -> Any:
    return json.loads(
        data, parse_constant=_reject_non_finite, parse_float=_finite_float, object_pairs_hook=_unique_keys
    )


def _table_schema(conn: sqlite3.Connection, table: str) -> list[sqlite3.Row]:
    return conn.execute(f"PRAGMA table_info({table})").fetchall()


def _column_value_matches(value: Any, column: sqlite3.Row) -> bool:
    if value is None:
        return not (column["notnull"] or column["pk"])
    kind = column["type"]
    if kind == "INTEGER":
        return type(value) is int and -(1 << 63) <= value < (1 << 63)
    if kind == "REAL":
        # SQLite presents values read from REAL columns as floats.
        return type(value) is float and math.isfinite(value)
    return kind == "TEXT" and type(value) is str


def _validate_rows(table: str, rows: Any, schema: list[sqlite3.Row], game_id: str) -> None:
    names = {column["name"] for column in schema}
    if not {"game_id", *_JSON_COLUMNS[table]} <= names or not isinstance(rows, list):
        raise ValueError(f"Invalid checkpoint table {table}")

    for row in rows:
        if not isinstance(row, dict) or row.keys() != names or row["game_id"] != game_id:
            raise ValueError(f"Invalid or cross-game row in checkpoint table {table}")
        for column in schema:
            name = column["name"]
            if not _column_value_matches(row[name], column):
                raise ValueError(f"Invalid checkpoint value at {table}.{name}")
        for name, expected in _JSON_COLUMNS[table].items():
            if row[name] is not None and not isinstance(_load_json(row[name]), expected):
                raise ValueError(f"Invalid checkpoint JSON value at {table}.{name}")


def encode_snapshot(conn: sqlite3.Connection, game_id: str, *, simulation: bool) -> str:
    """Read all four tables from the transaction that will insert the checkpoint."""
    if not conn.in_transaction:
        raise RuntimeError("Checkpoint creation requires an open transaction")

    tables: dict[str, list[dict[str, Any]]] = {}
    for table in _TABLES:
        rows = [dict(row) for row in conn.execute(
            f"SELECT * FROM {table} WHERE game_id = ?", (game_id,)
        )]
        _validate_rows(table, rows, _table_schema(conn, table), game_id)
        tables[table] = rows
    return json.dumps({
        "format_version": _FORMAT_VERSION,
        "game_id": game_id,
        "simulation": simulation,
        "tables": tables,
    }, allow_nan=False, separators=(",", ":"))


def _quote_identifier(identifier: str) -> str:
    # Only names from the fixed allowlist and the database schema reach SQL.
    return '"' + identifier.replace('"', '""') + '"'


def restore_snapshot(conn: sqlite3.Connection, game_id: str, data: str, *, simulation: bool) -> None:
    """Apply a validated snapshot inside the caller's transaction, or raise."""
    if not conn.in_transaction:
        raise RuntimeError("Checkpoint restoration requires an open transaction")
    if not isinstance(data, str):
        raise ValueError("Checkpoint payload is not text")

    snapshot = _load_json(data)
    if not isinstance(snapshot, dict):
        raise ValueError("Invalid checkpoint payload")
    version = snapshot.get("format_version")
    if type(version) is not int or version != _FORMAT_VERSION:
        raise ValueError("Legacy or unsupported checkpoint format; create a new checkpoint")
    if snapshot.get("game_id") != game_id or snapshot.get("simulation") is not simulation:
        raise ValueError("Checkpoint game or evidence domain does not match")
    tables = snapshot.get("tables")
    if not isinstance(tables, dict) or tables.keys() != set(_TABLES):
        raise ValueError("Checkpoint is missing tables or contains unknown tables")

    for table in _TABLES:
        rows = tables[table]
        schema = _table_schema(conn, table)
        _validate_rows(table, rows, schema, game_id)
        conn.execute(f"DELETE FROM {table} WHERE game_id = ?", (game_id,))
        if rows:
            names = [column["name"] for column in schema]
            columns = ", ".join(map(_quote_identifier, names))
            placeholders = ", ".join("?" for _ in names)
            # No REPLACE: a duplicate key or a collision with another game's
            # surrogate ID is an integrity error and rolls back all four tables.
            conn.executemany(
                f"INSERT INTO {table} ({columns}) VALUES ({placeholders})",
                (tuple(row[name] for name in names) for row in rows),
            )
