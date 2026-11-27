"""
Universal Game MCP (UGM) - Persistent Knowledge Storage Engine
Implements PersistentKnowledgeStorageContract with game partitioning,
WAL-backed SQLite persistence, source attribution ('live' vs 'synthetic_fixture'),
bounded history pruning, and export/import.
"""

from __future__ import annotations
import os
import json
import time
import tempfile
import logging
import sqlite3
from statistics import median
from uuid import uuid4
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, asdict

from .checkpoints import encode_snapshot, restore_snapshot
from .database import DatabaseManager, compute_dhash, hamming_distance
from .skill_optimization import (
    MAX_BENCHMARK_PAIRS, MIN_BENCHMARK_PAIRS, MIN_MEDIAN_SPEEDUP,
    SkillBenchmarkSample, SkillOptimizationCandidate, canonical_json, capture_ids,
    definition_fingerprint, next_candidate_version, valid_benchmark_pair,
)
from ..skills.contracts import PersistentKnowledgeStorageContract
from ..core.safety import SafetyViolationError

from .transition_quarantine import get_quarantined_transitions, quarantine_transition, transition_identity

logger = logging.getLogger("ugm.memory.storage")
_RECOVERY_EVIDENCE_MAX_AGE_S = 30 * 86400


@dataclass
class UIStateRecord:
    id: int
    game_id: str
    state_name: str
    phash: str
    confidence: float
    evidence: Dict[str, Any]
    metadata: Dict[str, Any]
    source: str
    first_seen: float
    last_seen: float
    occurrences: int


@dataclass
class TransitionRecord:
    id: int
    game_id: str
    from_state: str
    to_state: str
    action_type: str
    params: Dict[str, Any]
    cost: float
    success_count: int
    fail_count: int
    confidence: float
    evidence: Dict[str, Any]
    source: str
    created_at: float
    updated_at: float
    status: str = "candidate"  # 'candidate', 'verified', 'suspect', 'rejected'
    consecutive_failures: int = 0


@dataclass
class LearnedSkillRecord:
    skill_id: str
    game_id: str
    version: str
    name: str
    description: str
    preconditions: Dict[str, Any]
    steps: List[Dict[str, Any]]
    post_verification: Dict[str, Any]
    status: str  # 'candidate', 'verified', 'suspect', 'rejected'
    validation_count: int
    success_count: int
    fail_count: int
    confidence: float
    source: str
    created_at: float
    updated_at: float
    consecutive_failures: int = 0
    optimization_notes: Optional[str] = None


class PersistentKnowledgeStorage(PersistentKnowledgeStorageContract):
    """
    Persistent SQLite storage engine for game states, navigation transitions,
    and verified self-learned skills.
    """

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager()

    # ---------------------------------------------------------
    # Game Registration
    # ---------------------------------------------------------
    def register_game(
        self,
        game_id: str,
        name: str,
        version: str = "1.0.0",
        base_res: Tuple[int, int] = (1920, 1080),
        ui_config: Optional[Dict[str, Any]] = None
    ) -> None:
        """Registers or updates a game metadata entry."""
        conn = self.db.get_connection()
        now = time.time()
        with conn:
            conn.execute("""
                INSERT INTO games (game_id, name, version, base_res_w, base_res_h, ui_config, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(game_id) DO UPDATE SET
                    name=excluded.name,
                    version=excluded.version,
                    base_res_w=excluded.base_res_w,
                    base_res_h=excluded.base_res_h,
                    ui_config=excluded.ui_config,
                    updated_at=excluded.updated_at
            """, (
                game_id, name, version, base_res[0], base_res[1],
                json.dumps(ui_config or {}), now, now
            ))

    def get_game(self, game_id: str) -> Optional[Dict[str, Any]]:
        conn = self.db.get_connection()
        cur = conn.execute("SELECT * FROM games WHERE game_id = ?", (game_id,))
        row = cur.fetchone()
        if not row:
            return None
        return dict(row)

    def list_games(self) -> List[Dict[str, Any]]:
        conn = self.db.get_connection()
        cur = conn.execute("SELECT * FROM games ORDER BY game_id")
        return [dict(r) for r in cur.fetchall()]

    # ---------------------------------------------------------
    # UI State Knowledge
    # ---------------------------------------------------------
    def record_ui_state(
        self,
        game_id: str,
        state_name: str,
        phash: str,
        confidence: float = 1.0,
        evidence: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        source: str = "live"
    ) -> int:
        """Records an observed UI state or increments its occurrence count."""
        conn = self.db.get_connection()
        now = time.time()
        ev_json = json.dumps(evidence or {})
        meta_json = json.dumps(metadata or {})

        with conn:
            cur = conn.execute("""
                INSERT INTO ui_states (
                    game_id, state_name, phash, confidence, evidence, metadata, source, first_seen, last_seen, occurrences
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(game_id, state_name, phash) DO UPDATE SET
                    occurrences = occurrences + 1,
                    last_seen = excluded.last_seen,
                    confidence = MAX(confidence, excluded.confidence),
                    evidence = excluded.evidence
                RETURNING id;
            """, (game_id, state_name, phash, confidence, ev_json, meta_json, source, now, now))
            row = cur.fetchone()
            state_id = row[0] if row else 0

        self._prune_states_if_needed(game_id, max_states=1000)
        return state_id

    record_state = record_ui_state

    def get_states(self, game_id: str, source: Optional[str] = None) -> List[UIStateRecord]:
        conn = self.db.get_connection()
        query = "SELECT * FROM ui_states WHERE game_id = ?"
        params: List[Any] = [game_id]
        if source:
            query += " AND source = ?"
            params.append(source)
        query += " ORDER BY last_seen DESC"

        cur = conn.execute(query, tuple(params))
        results = []
        for r in cur.fetchall():
            results.append(UIStateRecord(
                id=r["id"],
                game_id=r["game_id"],
                state_name=r["state_name"],
                phash=r["phash"],
                confidence=r["confidence"],
                evidence=json.loads(r["evidence"] or "{}"),
                metadata=json.loads(r["metadata"] or "{}"),
                source=r["source"],
                first_seen=r["first_seen"],
                last_seen=r["last_seen"],
                occurrences=r["occurrences"]
            ))
        return results

    def find_matching_state(
        self,
        game_id: str,
        phash: str,
        max_distance: int = 6
    ) -> Optional[Tuple[str, float, int]]:
        """
        Finds the closest known state for game_id using perceptual hash Hamming distance.
        Returns (state_name, confidence, distance) or None.
        """
        conn = self.db.get_connection()
        cur = conn.execute(
            "SELECT state_name, phash, confidence FROM ui_states WHERE game_id = ? ORDER BY occurrences DESC",
            (game_id,)
        )
        best_match = None
        min_dist = 999

        for row in cur.fetchall():
            dist = hamming_distance(phash, row["phash"])
            if dist <= max_distance and dist < min_dist:
                min_dist = dist
                best_match = (row["state_name"], row["confidence"], dist)

        return best_match

    # ---------------------------------------------------------
    # Navigation Transitions (Graph Edges)
    # ---------------------------------------------------------
    def record_transition(
        self,
        game_id: str,
        from_state: str,
        to_state: str,
        action_type: str,
        params: Dict[str, Any],
        cost: float = 1.0,
        success: bool = True,
        evidence: Optional[Dict[str, Any]] = None,
        source: str = "live",
        is_execution_error: bool = False,
        status: Optional[str] = None
    ) -> None:
        """Records a verified or failed navigation transition between states with 4-state lifecycle."""
        conn = self.db.get_connection()
        now = time.time()
        key = transition_identity(from_state, to_state, action_type, params)
        param_str = key[-1]
        observed = dict(evidence or {})
        for key_name in ("native_success_streak", "native_success_at", "previous_native_success_at"):
            observed.pop(key_name, None)

        if is_execution_error:
            # Execution error (transient OS / window glitch) does NOT penalize transition knowledge
            logger.info("Transition execution error recorded without penalizing knowledge state.")
            return

        with conn:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute("""
                SELECT id, success_count, fail_count, confidence, status, consecutive_failures, evidence, source, updated_at
                FROM navigation_transitions
                WHERE game_id = ? AND from_state = ? AND to_state = ? AND action_type = ? AND params = ?
            """, (game_id, from_state, to_state, action_type, param_str))
            row = cur.fetchone()
            if (
                not self.db.simulation and success and source == "live"
                and observed.get("producer") == "scoped_native_navigation"
                and observed.get("evidence_level") == "verified"
                and observed.get("observed_state") == to_state
            ):
                previous = {}
                if (row and row["source"] == "live"
                        and 0 <= now - row["updated_at"] <= _RECOVERY_EVIDENCE_MAX_AGE_S):
                    previous = json.loads(row["evidence"] or "{}")
                previous_at = previous.get("native_success_at") if isinstance(previous, dict) else None
                observed["native_success_at"] = now
                if type(previous_at) in (int, float) and 0 <= now - previous_at <= _RECOVERY_EVIDENCE_MAX_AGE_S:
                    observed["previous_native_success_at"] = previous_at
            ev_str = json.dumps(observed)


            if not row:
                init_status = status or ("verified" if success else "candidate")
                succ_cnt = 1 if success else 0
                fail_cnt = 0 if success else 1
                conf = 1.0 if success else 0.0
                c_fail = 0 if success else 1
                conn.execute("""
                    INSERT INTO navigation_transitions (
                        game_id, from_state, to_state, action_type, params, cost, success_count, fail_count,
                        confidence, evidence, source, created_at, updated_at, status, consecutive_failures
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    game_id, from_state, to_state, action_type, param_str, cost,
                    succ_cnt, fail_cnt, conf, ev_str, source, now, now, init_status, c_fail
                ))
                if init_status == "rejected":
                    quarantine_transition(conn, game_id, key, now, "recorded_rejected_transition")
            else:
                succ_cnt = row["success_count"] + (1 if success else 0)
                fail_cnt = row["fail_count"] + (0 if success else 1)
                conf = round(succ_cnt / max(1, succ_cnt + fail_cnt), 3)
                curr_status = row["status"] or "candidate"
                c_fail = row["consecutive_failures"] or 0

                if success:
                    c_fail = 0
                    if curr_status == "candidate" and succ_cnt >= 2 and conf >= 0.8:
                        new_status = "verified"
                    elif curr_status == "suspect":
                        new_status = "verified"
                    else:
                        new_status = curr_status
                else:
                    c_fail += 1
                    if curr_status == "verified" and c_fail >= 2:
                        new_status = "suspect"
                    elif curr_status == "candidate" and c_fail >= 3:
                        new_status = "rejected"
                    elif curr_status == "suspect" and c_fail >= 4:
                        new_status = "rejected"
                    else:
                        new_status = curr_status

                if status is not None:
                    new_status = status

                conn.execute("""
                    UPDATE navigation_transitions SET
                        success_count = ?,
                        fail_count = ?,
                        confidence = ?,
                        cost = ?,
                        evidence = ?,
                        updated_at = ?,
                        status = ?,
                        consecutive_failures = ?
                    WHERE id = ?
                """, (succ_cnt, fail_cnt, conf, cost, ev_str, now, new_status, c_fail, row["id"]))
                if new_status == "rejected":
                    quarantine_transition(conn, game_id, key, now, "recorded_rejected_transition")


    save_transition = record_transition

    def quarantine_route(self, game_id: str, from_state: str, to_state: str,
                         action_type: str, params: Dict[str, Any], reason: str = "") -> bool:
        """Suppress an exact route even if a curated edge has no historical row."""
        if not isinstance(game_id, str) or not game_id.strip():
            raise ValueError("Transition quarantine requires a game_id.")
        key = transition_identity(from_state, to_state, action_type, params)
        conn = self.db.get_connection()
        now = time.time()
        with conn:
            quarantine_transition(conn, game_id, key, now, reason or "explicit_route_rejection")
            conn.execute("""
                UPDATE navigation_transitions SET status = 'rejected', updated_at = ?
                WHERE game_id = ? AND from_state = ? AND to_state = ? AND action_type = ? AND params = ?
            """, (now, game_id, *key))
        return True

    def get_quarantined_routes(self, game_id: str, from_state: Optional[str] = None) -> set:
        return get_quarantined_transitions(self.db.get_connection(), game_id, from_state)

    def get_transitions(
        self,
        game_id: str,
        from_state: Optional[str] = None,
        min_confidence: float = 0.0,
        status: Optional[str] = None,
        exclude_rejected: bool = True
    ) -> List[TransitionRecord]:
        """Retrieves verified navigation edges for pathfinding."""
        conn = self.db.get_connection()
        query = "SELECT * FROM navigation_transitions WHERE game_id = ? AND confidence >= ?"
        params: List[Any] = [game_id, min_confidence]
        if from_state:
            query += " AND from_state = ?"
            params.append(from_state)
        if status:
            query += " AND status = ?"
            params.append(status)
        elif exclude_rejected:
            query += " AND (status IS NULL OR status != 'rejected')"
        query += " ORDER BY cost ASC"

        cur = conn.execute(query, tuple(params))
        results = []
        for r in cur.fetchall():
            results.append(TransitionRecord(
                id=r["id"],
                game_id=r["game_id"],
                from_state=r["from_state"],
                to_state=r["to_state"],
                action_type=r["action_type"],
                params=json.loads(r["params"]),
                cost=r["cost"],
                success_count=r["success_count"],
                fail_count=r["fail_count"],
                confidence=r["confidence"],
                evidence=json.loads(r["evidence"] or "{}"),
                source=r["source"],
                created_at=r["created_at"],
                updated_at=r["updated_at"],
                status=r["status"] if "status" in r.keys() else "candidate",
                consecutive_failures=r["consecutive_failures"] if "consecutive_failures" in r.keys() else 0
            ))
        return results

    def recovery_destinations(self, game_id: str, state_name: str, limit: int = 4) -> list[str]:
        """Get only bounded same-game destinations with recent scoped native proof."""
        if self.db.simulation or not 1 <= limit <= 4:
            return []
        now = time.time()
        conn = self.db.get_connection()
        rows = conn.execute("""
            SELECT t.to_state
            FROM navigation_transitions t
            WHERE t.game_id = ? AND t.from_state = ? AND t.to_state != t.from_state
              AND lower(trim(t.to_state)) NOT IN ('unknown', 'ambiguous', '')
              AND t.status = 'verified' AND t.source = 'live' AND t.confidence >= .8
              AND t.consecutive_failures = 0
              AND t.action_type IN ('press_keys', 'click_target')
              AND CASE WHEN json_valid(t.evidence) THEN
                    json_extract(t.evidence, '$.producer') = 'scoped_native_navigation'
                AND json_extract(t.evidence, '$.evidence_level') = 'verified'
                AND json_extract(t.evidence, '$.observed_state') = t.to_state
                AND json_type(t.evidence, '$.previous_native_success_at') IN ('integer', 'real')
                AND json_extract(t.evidence, '$.previous_native_success_at') BETWEEN ? AND ?
                AND json_extract(t.evidence, '$.native_success_at')
                    BETWEEN json_extract(t.evidence, '$.previous_native_success_at') AND ?
              ELSE 0 END
              AND NOT EXISTS (
                  SELECT 1 FROM transition_quarantine q
                  WHERE (q.game_id, q.from_state, q.to_state, q.action_type, q.params)
                      = (t.game_id, t.from_state, t.to_state, t.action_type, t.params)
              )
            GROUP BY t.to_state
            ORDER BY MIN(t.cost), t.to_state
            LIMIT ?
        """, (game_id, state_name, now - _RECOVERY_EVIDENCE_MAX_AGE_S, now, now, limit))
        return [row["to_state"] for row in rows]

    # ---------------------------------------------------------
    # Action History
    # ---------------------------------------------------------
    def record_action_history(
        self,
        game_id: str,
        action_name: str,
        params: Dict[str, Any],
        success: bool,
        from_state: Optional[str] = None,
        to_state: Optional[str] = None,
        reason: str = "",
        duration_ms: float = 0.0,
        source: str = "live"
    ) -> None:
        """Logs an action execution event."""
        conn = self.db.get_connection()
        now = time.time()
        with conn:
            conn.execute("""
                INSERT INTO action_history (
                    game_id, from_state, to_state, action_name, params, success, reason, duration_ms, source, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                game_id, from_state, to_state, action_name, json.dumps(params),
                1 if success else 0, reason, duration_ms, source, now
            ))
        self._prune_history_if_needed(game_id, max_entries=5000)

    def get_action_history(self, game_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        conn = self.db.get_connection()
        cur = conn.execute(
            "SELECT * FROM action_history WHERE game_id = ? ORDER BY created_at DESC LIMIT ?",
            (game_id, limit)
        )
        return [dict(r) for r in cur.fetchall()]

    # ---------------------------------------------------------
    # Learned Skills
    # ---------------------------------------------------------
    def save_learned_skill(
        self,
        *args,
        skill_id: Optional[str] = None,
        game_id: Optional[str] = None,
        name: Optional[str] = None,
        steps: Optional[Any] = None,
        description: str = "",
        preconditions: Optional[Dict[str, Any]] = None,
        post_verification: Optional[Dict[str, Any]] = None,
        status: str = "candidate",
        source: str = "live",
        initial_confidence: Optional[float] = None,
        confidence: Optional[float] = None,
        tags: Optional[List[str]] = None,
        **kwargs
    ) -> bool:
        """Save a new skill or refresh an unverified proposal, never rewrite verified behavior."""
        if args:
            if len(args) == 4 and isinstance(args[3], (list, str)):
                # (skill_id, game_id, name, steps)
                skill_id = args[0]
                game_id = args[1]
                name = args[2]
                steps = args[3]
            elif len(args) >= 5 and isinstance(args[4], (list, str)):
                # (game_id, skill_id, name, description, steps)
                game_id = args[0]
                skill_id = args[1]
                name = args[2]
                description = args[3]
                steps = args[4]
            else:
                if len(args) > 0 and skill_id is None: skill_id = args[0]
                if len(args) > 1 and game_id is None: game_id = args[1]
                if len(args) > 2 and name is None: name = args[2]
                if len(args) > 3 and steps is None: steps = args[3]
                if len(args) > 4 and not description: description = args[4]

        conn = self.db.get_connection()
        steps_value = json.loads(steps) if isinstance(steps, str) else steps or []
        steps_json = canonical_json(steps_value)
        pre_json = canonical_json(preconditions or {})
        post_json = canonical_json(post_verification or {})
        init_conf = confidence if confidence is not None else (initial_confidence if initial_confidence is not None else 0.5)

        with conn:
            conn.execute("BEGIN IMMEDIATE")
            active = self._find_skill_row(conn, skill_id, game_id) if game_id is not None else None
            now = time.time()
            if active is None:
                conn.execute("""
                    INSERT INTO learned_skills (
                        skill_id, game_id, version, name, description, preconditions, steps,
                        post_verification, status, validation_count, success_count, fail_count,
                        confidence, source, created_at, updated_at
                    ) VALUES (?, ?, '1.0.0', ?, ?, ?, ?, ?, ?, 0, 0, 0, ?, ?, ?, ?)
                """, (skill_id, game_id, name, description, pre_json, steps_json, post_json, status,
                      init_conf, source, now, now))
                return True

            same_program = (
                canonical_json(json.loads(active["steps"])) == steps_json
                and canonical_json(json.loads(active["preconditions"] or "{}")) == pre_json
                and canonical_json(json.loads(active["post_verification"] or "{}")) == post_json
            )
            if active["status"] != "candidate" or status != "candidate":
                # A retry of an existing definition is an idempotent no-op. An
                # ordinary learning/import call cannot replace or reclassify it.
                return status == "candidate" and same_program

            if same_program:
                conn.execute("""
                    UPDATE learned_skills SET name = ?, description = ?, updated_at = ?
                    WHERE game_id = ? AND skill_id = ?
                """, (name, description, now, game_id, skill_id))
                return True

            reserved = {
                r["version"] for r in conn.execute(
                    "SELECT version FROM skill_optimization_candidates WHERE game_id = ? AND skill_id = ?",
                    (game_id, skill_id),
                )
            }
            version = next_candidate_version(active["version"], reserved, None)
            conn.execute("""
                UPDATE learned_skills
                SET name = ?, description = ?, preconditions = ?, steps = ?, post_verification = ?,
                    version = ?, validation_count = 0, success_count = 0, fail_count = 0,
                    consecutive_failures = 0, confidence = ?, source = ?, updated_at = ?
                WHERE game_id = ? AND skill_id = ?
            """, (name, description, pre_json, steps_json, post_json, version,
                  init_conf, source, now, game_id, skill_id))
            conn.execute("""
                DELETE FROM skill_optimization_candidates
                WHERE game_id = ? AND skill_id = ? AND promoted_at IS NULL
            """, (game_id, skill_id))
            return True

    @staticmethod
    def _find_skill_row(conn, skill_id: str, game_id: Optional[str]):
        if game_id is not None:
            return conn.execute(
                "SELECT * FROM learned_skills WHERE game_id = ? AND skill_id = ?",
                (game_id, skill_id),
            ).fetchone()

        # Legacy callers may omit the game only if the identifier is unique.
        matches = conn.execute(
            "SELECT * FROM learned_skills WHERE skill_id = ? LIMIT 2", (skill_id,)
        ).fetchall()
        return matches[0] if len(matches) == 1 else None

    def update_skill_stats(
        self,
        skill_id: str,
        success: bool,
        auto_promote_threshold: int = 3,
        is_execution_error: bool = False,
        reason: str = "",
        latency_ms: Optional[float] = None,
        *,
        game_id: Optional[str] = None,
        expected_version: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Updates validation stats and manages 4-state lifecycle (candidate, verified, suspect, rejected).
        Critical invariant: One isolated failure must never overwrite previously verified knowledge.
        Distinguishes transient execution errors from true rule/postcondition failures.
        """
        conn = self.db.get_connection()
        now = time.time()
        with conn:
            # Lock before reading counts so concurrent validations cannot
            # overwrite each other's increments or change legacy ID resolution.
            conn.execute("BEGIN IMMEDIATE")
            row = self._find_skill_row(conn, skill_id, game_id)
            if row is None or (expected_version is not None and row["version"] != expected_version):
                return {"skill_id": skill_id, "updated": False}
            key = (row["game_id"], row["skill_id"])

            curr_status = row["status"]
            v_count = row["validation_count"]
            s_count = row["success_count"]
            f_count = row["fail_count"]
            consec_fail = row["consecutive_failures"] if ("consecutive_failures" in row.keys() and row["consecutive_failures"] is not None) else 0

            if is_execution_error:
                # Transient environment / input failure: do NOT penalize rule confidence or change knowledge state!
                f_count += 1
                conn.execute(
                    "UPDATE learned_skills SET fail_count = ?, updated_at = ? WHERE game_id = ? AND skill_id = ?",
                    (f_count, now, *key),
                )
                logger.info("Skill '%s' execution error recorded without penalizing knowledge status (%s).", skill_id, reason)
                return {
                    "skill_id": skill_id,
                    "status": curr_status,
                    "confidence": row["confidence"],
                    "consecutive_failures": consec_fail,
                    "fail_count": f_count,
                    "failure_count": f_count,
                    "execution_error": True,
                    "updated": True
                }

            v_count += 1
            if success:
                s_count += 1
            else:
                f_count += 1

            confidence = round(s_count / max(1, s_count + f_count), 3)
            new_status = curr_status

            if success:
                if curr_status == "suspect":
                    # Require 2 consecutive recovery successes to restore to verified
                    if consec_fail > 0:
                        consec_fail = -1
                    else:
                        consec_fail -= 1
                    if abs(consec_fail) >= 2:
                        new_status = "verified"
                        consec_fail = 0
                        logger.info("Skill '%s' restored to VERIFIED after successful validation recovery.", skill_id)
                else:
                    consec_fail = 0

                if curr_status == "candidate" and v_count >= auto_promote_threshold and confidence >= 0.8:
                    new_status = "verified"
                    logger.info("Skill '%s' promoted to VERIFIED after %d successful validations.", skill_id, v_count)
            else:
                if consec_fail < 0:
                    consec_fail = 0
                consec_fail += 1

                if curr_status == "verified":
                    if consec_fail >= 2:
                        # Repeated verified postcondition failures -> demote to suspect
                        new_status = "suspect"
                        logger.warning("Verified skill '%s' transitioned to SUSPECT after %d consecutive failures.", skill_id, consec_fail)
                    else:
                        logger.info("Isolated failure on verified skill '%s' retained as VERIFIED (failure 1/2).", skill_id)
                elif curr_status == "candidate" and consec_fail >= 3:
                    new_status = "rejected"
                    logger.warning("Candidate skill '%s' marked REJECTED after %d consecutive failures.", skill_id, consec_fail)
                elif curr_status == "suspect" and consec_fail >= 4:
                    new_status = "rejected"
                    logger.warning("Suspect skill '%s' marked REJECTED after repeated failures.", skill_id)

            conn.execute("""
                UPDATE learned_skills SET
                    validation_count = ?,
                    success_count = ?,
                    fail_count = ?,
                    confidence = ?,
                    status = ?,
                    consecutive_failures = ?,
                    updated_at = ?
                WHERE game_id = ? AND skill_id = ?
            """, (v_count, s_count, f_count, confidence, new_status, consec_fail, now, *key))

            return {
                "skill_id": skill_id,
                "status": new_status,
                "previous_status": curr_status,
                "confidence": confidence,
                "validation_count": v_count,
                "consecutive_failures": consec_fail,
                "updated": True
            }

    def get_learned_skill(self, skill_id: str, *, game_id: Optional[str] = None) -> Optional[LearnedSkillRecord]:
        """Retrieve a game-scoped skill, or a uniquely named legacy skill."""
        conn = self.db.get_connection()
        r = self._find_skill_row(conn, skill_id, game_id)
        if not r:
            return None
        return LearnedSkillRecord(
            skill_id=r["skill_id"],
            game_id=r["game_id"],
            version=r["version"],
            name=r["name"],
            description=r["description"],
            preconditions=json.loads(r["preconditions"] or "{}"),
            steps=json.loads(r["steps"] or "[]"),
            post_verification=json.loads(r["post_verification"] or "{}"),
            status=r["status"],
            validation_count=r["validation_count"],
            success_count=r["success_count"],
            fail_count=r["fail_count"],
            confidence=r["confidence"],
            source=r["source"],
            created_at=r["created_at"],
            updated_at=r["updated_at"],
            consecutive_failures=r["consecutive_failures"] if "consecutive_failures" in r.keys() else 0,
            optimization_notes=r["optimization_notes"] if "optimization_notes" in r.keys() else None
        )

    def list_learned_skills(
        self,
        game_id: Optional[str] = None,
        status: Optional[str] = None
    ) -> List[LearnedSkillRecord]:
        conn = self.db.get_connection()
        query = "SELECT * FROM learned_skills WHERE 1=1"
        params: List[Any] = []
        if game_id:
            query += " AND game_id = ?"
            params.append(game_id)
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY confidence DESC, validation_count DESC"

        cur = conn.execute(query, tuple(params))
        skills = []
        for r in cur.fetchall():
            skills.append(LearnedSkillRecord(
                skill_id=r["skill_id"],
                game_id=r["game_id"],
                version=r["version"],
                name=r["name"],
                description=r["description"],
                preconditions=json.loads(r["preconditions"] or "{}"),
                steps=json.loads(r["steps"] or "[]"),
                post_verification=json.loads(r["post_verification"] or "{}"),
                status=r["status"],
                validation_count=r["validation_count"],
                success_count=r["success_count"],
                fail_count=r["fail_count"],
                confidence=r["confidence"],
                source=r["source"],
                created_at=r["created_at"],
                updated_at=r["updated_at"],
                consecutive_failures=r["consecutive_failures"] if "consecutive_failures" in r.keys() else 0,
                optimization_notes=r["optimization_notes"] if "optimization_notes" in r.keys() else None
            ))
        return skills

    def get_learned_skills(self, game_id: Optional[str] = None, status: Optional[str] = None) -> List[Dict[str, Any]]:
        """Returns learned skills as dictionaries (convenience method for V2 exploration)."""
        records = self.list_learned_skills(game_id=game_id, status=status)
        result = []
        for r in records:
            d = asdict(r)
            d["failure_count"] = r.fail_count
            result.append(d)
        return result

    def get_ui_states(self, game_id: str, source: Optional[str] = None) -> List[Any]:
        """Returns recorded UI states for a game."""
        return self.get_states(game_id=game_id, source=source)

    def delete_learned_skill(self, skill_id: str, *, game_id: Optional[str] = None) -> bool:
        conn = self.db.get_connection()
        with conn:
            if game_id is None:
                conn.execute("BEGIN IMMEDIATE")
                row = self._find_skill_row(conn, skill_id, game_id=None)
                if row is None:
                    return False
                game_id = row["game_id"]
            cur = conn.execute(
                "DELETE FROM learned_skills WHERE game_id = ? AND skill_id = ?", (game_id, skill_id)
            )
            if cur.rowcount:
                conn.execute(
                    "DELETE FROM skill_optimization_candidates WHERE game_id = ? AND skill_id = ?",
                    (game_id, skill_id),
                )
            return cur.rowcount > 0

    # ---------------------------------------------------------
    # Contract Implementation: PersistentKnowledgeStorageContract
    # ---------------------------------------------------------
    def save_knowledge(self, game_id: str, key: str, value: Dict[str, Any]) -> bool:
        """Stores arbitrary structured game knowledge entry."""
        conn = self.db.get_connection()
        now = time.time()
        val_json = json.dumps(value)
        with conn:
            conn.execute("""
                INSERT INTO selectors_memory (game_id, selector_id, state_name, selector_data, confidence, success_count, fail_count, last_verified, source)
                VALUES (?, ?, 'global', ?, 1.0, 1, 0, ?, 'live')
                ON CONFLICT(game_id, selector_id, state_name) DO UPDATE SET
                    selector_data = excluded.selector_data,
                    last_verified = excluded.last_verified
            """, (game_id, key, val_json, now))
        return True

    def get_knowledge(self, game_id: str, key: str) -> Optional[Dict[str, Any]]:
        conn = self.db.get_connection()
        cur = conn.execute(
            "SELECT selector_data FROM selectors_memory WHERE game_id = ? AND selector_id = ?",
            (game_id, key)
        )
        row = cur.fetchone()
        if not row:
            return None
        return json.loads(row["selector_data"])

    def list_knowledge_keys(self, game_id: str) -> List[str]:
        conn = self.db.get_connection()
        cur = conn.execute(
            "SELECT selector_id FROM selectors_memory WHERE game_id = ?",
            (game_id,)
        )
        return [r["selector_id"] for r in cur.fetchall()]

    # ---------------------------------------------------------
    # Memory Maintenance, Export, Import, Reset
    # ---------------------------------------------------------
    def get_statistics(self, game_id: Optional[str] = None) -> Dict[str, Any]:
        """Returns summary statistics of the persistent knowledge store."""
        conn = self.db.get_connection()
        g_filter = "WHERE game_id = ?" if game_id else ""
        params = (game_id,) if game_id else ()

        cur_states = conn.execute(f"SELECT COUNT(*) FROM ui_states {g_filter}", params).fetchone()[0]
        cur_trans = conn.execute(f"SELECT COUNT(*) FROM navigation_transitions {g_filter}", params).fetchone()[0]
        cur_actions = conn.execute(f"SELECT COUNT(*) FROM action_history {g_filter}", params).fetchone()[0]
        cur_skills = conn.execute(f"SELECT COUNT(*) FROM learned_skills {g_filter}", params).fetchone()[0]
        cur_verified = conn.execute(
            f"SELECT COUNT(*) FROM learned_skills {'WHERE game_id = ? AND' if game_id else 'WHERE'} status = 'verified'",
            params
        ).fetchone()[0]

        return {
            "game_id": game_id or "all_games",
            "ui_states_count": cur_states,
            "transitions_count": cur_trans,
            "action_history_count": cur_actions,
            "total_learned_skills": cur_skills,
            "verified_skills": cur_verified,
            "candidate_skills": cur_skills - cur_verified,
            "db_path": self.db.db_path
        }

    def delete_memory(
        self,
        game_id: Optional[str] = None,
        scope: str = "all"
    ) -> Dict[str, Any]:
        """
        Safely deletes scoped memory:
        - scope='all': deletes everything for game_id (or whole DB if game_id is None)
        - scope='history': deletes only action history
        - scope='skills': deletes only learned skills
        - scope='transitions': deletes navigation graph transitions
        """
        conn = self.db.get_connection()
        deleted_counts: Dict[str, int] = {}
        with conn:
            if scope in ("all", "history"):
                q = "DELETE FROM action_history" + (" WHERE game_id = ?" if game_id else "")
                deleted_counts["actions"] = conn.execute(q, (game_id,) if game_id else ()).rowcount
            if scope in ("all", "transitions"):
                q = "DELETE FROM navigation_transitions" + (" WHERE game_id = ?" if game_id else "")
                deleted_counts["transitions"] = conn.execute(q, (game_id,) if game_id else ()).rowcount
            if scope in ("all", "skills"):
                q = "DELETE FROM learned_skills" + (" WHERE game_id = ?" if game_id else "")
                deleted_counts["skills"] = conn.execute(q, (game_id,) if game_id else ()).rowcount
                q = "DELETE FROM skill_optimization_candidates" + (" WHERE game_id = ?" if game_id else "")
                conn.execute(q, (game_id,) if game_id else ())
            if scope in ("all", "states"):
                q = "DELETE FROM ui_states" + (" WHERE game_id = ?" if game_id else "")
                deleted_counts["states"] = conn.execute(q, (game_id,) if game_id else ()).rowcount
                q2 = "DELETE FROM selectors_memory" + (" WHERE game_id = ?" if game_id else "")
                deleted_counts["selectors"] = conn.execute(q2, (game_id,) if game_id else ()).rowcount
                q3 = "DELETE FROM semantic_selector_recipes" + (" WHERE game_id = ?" if game_id else "")
                deleted = conn.execute(q3, (game_id,) if game_id else ()).rowcount
                if deleted:
                    deleted_counts["semantic_selector_recipes"] = deleted
                q4 = "DELETE FROM provisional_screens" + (" WHERE game_id = ?" if game_id else "")
                deleted = conn.execute(q4, (game_id,) if game_id else ()).rowcount
                if deleted:
                    deleted_counts["provisional_screens"] = deleted

        return {"deleted": deleted_counts, "game_id": game_id, "scope": scope}

    def export_knowledge_bundle(self, game_id: str) -> Dict[str, Any]:
        """Exports game knowledge to a cross-model portable bundle."""
        states = [asdict(s) for s in self.get_states(game_id)]
        transitions = [asdict(t) for t in self.get_transitions(game_id)]
        skills = [asdict(k) for k in self.list_learned_skills(game_id)]
        return {
            "bundle_version": "1.5.0",
            "game_id": game_id,
            "exported_at": time.time(),
            "ui_states": states,
            "transitions": transitions,
            "learned_skills": skills
        }

    def import_knowledge_bundle(self, bundle: Dict[str, Any]) -> int:
        """Imports a knowledge bundle into local SQLite store."""
        game_id = bundle.get("game_id", "unknown")
        count = 0
        for sk in bundle.get("learned_skills", []):
            saved = self.save_learned_skill(
                skill_id=sk["skill_id"],
                game_id=game_id,
                name=sk["name"],
                steps=sk["steps"],
                description=sk.get("description", ""),
                preconditions=sk.get("preconditions"),
                post_verification=sk.get("post_verification"),
                status=sk.get("status", "candidate"),
                source=sk.get("source", "import")
            )
            count += int(saved)
        for tr in bundle.get("transitions", []):
            self.record_transition(
                game_id=game_id,
                from_state=tr["from_state"],
                to_state=tr["to_state"],
                action_type=tr["action_type"],
                params=tr["params"],
                success=True,
                cost=tr.get("cost", 1.0),
                source=tr.get("source", "import")
            )
            count += 1
        return count

    def _prune_history_if_needed(self, game_id: str, max_entries: int = 5000) -> None:
        """Keeps action history bounded to prevent unbounded disk growth."""
        conn = self.db.get_connection()
        cur = conn.execute("SELECT COUNT(*) FROM action_history WHERE game_id = ?", (game_id,))
        count = cur.fetchone()[0]
        if count > max_entries:
            delete_n = count - max_entries
            with conn:
                conn.execute("""
                    DELETE FROM action_history WHERE id IN (
                        SELECT id FROM action_history WHERE game_id = ? ORDER BY created_at ASC LIMIT ?
                    )
                """, (game_id, delete_n))

    def _prune_states_if_needed(self, game_id: str, max_states: int = 1000) -> None:
        """Prunes least frequently seen states if table grows excessively."""
        conn = self.db.get_connection()
        cur = conn.execute("SELECT COUNT(*) FROM ui_states WHERE game_id = ?", (game_id,))
        count = cur.fetchone()[0]
        if count > max_states:
            delete_n = count - max_states
            with conn:
                conn.execute("""
                    DELETE FROM ui_states WHERE id IN (
                        SELECT id FROM ui_states WHERE game_id = ? ORDER BY occurrences ASC, last_seen ASC LIMIT ?
                    )
                """, (game_id, delete_n))

    # ---------------------------------------------------------
    # V2 Knowledge Checkpointing & Rollback
    # ---------------------------------------------------------
    def create_checkpoint(self, game_id: str, label: str, description: str = "") -> int:
        """Save a consistent snapshot of one game's four mutable knowledge tables."""
        conn = self.db.get_connection()
        with conn:
            # A deferred read could race another writer before the checkpoint
            # row is committed. Acquire the write reservation before reading.
            conn.execute("BEGIN IMMEDIATE")
            now = time.time()
            payload = encode_snapshot(conn, game_id, simulation=self.db.simulation)
            cur = conn.execute("""
                INSERT INTO checkpoints (game_id, label, description, snapshot_data, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (game_id, label, description, payload, now))
            checkpoint_id = cur.lastrowid
        logger.info("Created knowledge checkpoint %d ('%s') for game '%s'.", checkpoint_id, label, game_id)
        return checkpoint_id

    def rollback_checkpoint(self, game_id: str, checkpoint_id: int) -> bool:
        """Restore complete versioned knowledge, or leave current rows unchanged."""
        conn = self.db.get_connection()
        try:
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT snapshot_data FROM checkpoints WHERE id = ? AND game_id = ?",
                    (checkpoint_id, game_id),
                ).fetchone()
                if row is None:
                    logger.warning("Checkpoint %d for game '%s' not found.", checkpoint_id, game_id)
                    return False
                restore_snapshot(conn, game_id, row["snapshot_data"], simulation=self.db.simulation)
                # These observations are not part of the legacy snapshot.
                # Force re-observation rather than trust potentially stale evidence.
                conn.execute("DELETE FROM semantic_selector_recipes WHERE game_id = ?", (game_id,))
                conn.execute("DELETE FROM provisional_screens WHERE game_id = ?", (game_id,))
                # Observation history for a not-yet-active candidate cannot
                # survive a rollback of the environment it was built against.
                # Keep already-promoted archives for audit.
                conn.execute(
                    "DELETE FROM skill_optimization_candidates WHERE game_id = ? AND promoted_at IS NULL",
                    (game_id,),
                )
        except (ValueError, RecursionError, sqlite3.IntegrityError) as exc:
            # Catch outside the transaction context so even a conflict in the
            # final table cannot commit an earlier portion of the restore.
            logger.warning("Refused checkpoint %d for game '%s': %s", checkpoint_id, game_id, exc)
            return False

        logger.info("Successfully rolled back game '%s' knowledge to checkpoint %d.", game_id, checkpoint_id)
        return True

    def list_checkpoints(self, game_id: str) -> List[Dict[str, Any]]:
        conn = self.db.get_connection()
        cur = conn.execute("SELECT id, game_id, label, description, created_at FROM checkpoints WHERE game_id = ? ORDER BY created_at DESC", (game_id,))
        return [dict(r) for r in cur.fetchall()]

    def delete_checkpoint(self, checkpoint_id: int) -> bool:
        conn = self.db.get_connection()
        with conn:
            cur = conn.execute("DELETE FROM checkpoints WHERE id = ?", (checkpoint_id,))
            return cur.rowcount > 0

    # ---------------------------------------------------------
    # V2 Knowledge State Transition Governance
    # ---------------------------------------------------------
    def update_knowledge_state(
        self,
        game_id: str,
        item_type: str,
        item_id: str,
        new_status: str,
        reason: str = ""
    ) -> bool:
        """
        Explicitly transitions knowledge status (candidate/verified/suspect/rejected).
        Supported item_types: 'skill', 'transition', 'selector'.
        """
        valid_statuses = {"candidate", "verified", "suspect", "rejected"}
        if new_status not in valid_statuses:
            raise ValueError(f"Invalid status '{new_status}'. Must be one of {valid_statuses}")

        conn = self.db.get_connection()
        now = time.time()
        with conn:
            if item_type == "skill":
                if new_status == "verified":
                    cur = conn.execute("UPDATE learned_skills SET status = ?, consecutive_failures = 0, updated_at = ? WHERE game_id = ? AND skill_id = ?", (new_status, now, game_id, item_id))
                else:
                    cur = conn.execute("UPDATE learned_skills SET status = ?, updated_at = ? WHERE game_id = ? AND skill_id = ?", (new_status, now, game_id, item_id))
                return cur.rowcount > 0
            elif item_type == "transition":
                if new_status == "verified":
                    cur = conn.execute("UPDATE navigation_transitions SET status = ?, consecutive_failures = 0, updated_at = ? WHERE game_id = ? AND id = ?", (new_status, now, game_id, int(item_id)))
                else:
                    cur = conn.execute("UPDATE navigation_transitions SET status = ?, updated_at = ? WHERE game_id = ? AND id = ?", (new_status, now, game_id, int(item_id)))
                if cur.rowcount > 0 and new_status == "rejected":
                    row = conn.execute("""
                        SELECT from_state, to_state, action_type, params FROM navigation_transitions
                        WHERE game_id = ? AND id = ?
                    """, (game_id, int(item_id))).fetchone()
                    key = transition_identity(
                        row["from_state"], row["to_state"], row["action_type"], json.loads(row["params"]),
                    )
                    quarantine_transition(conn, game_id, key, now, reason or "explicit_status_rejection")
                return cur.rowcount > 0
            elif item_type == "selector":
                if new_status == "verified":
                    cur = conn.execute("UPDATE selectors_memory SET status = ?, consecutive_failures = 0, last_verified = ? WHERE game_id = ? AND selector_id = ?", (new_status, now, game_id, item_id))
                else:
                    cur = conn.execute("UPDATE selectors_memory SET status = ?, last_verified = ? WHERE game_id = ? AND selector_id = ?", (new_status, now, game_id, item_id))
                return cur.rowcount > 0
            else:
                raise ValueError(f"Unknown item_type '{item_type}'. Must be 'skill', 'transition', or 'selector'.")

    # ---------------------------------------------------------
    # V2 Universal Cross-Game Concepts
    # ---------------------------------------------------------
    def get_cross_game_concepts(self, category: Optional[str] = None) -> List[Dict[str, Any]]:
        conn = self.db.get_connection()
        if category:
            cur = conn.execute("SELECT * FROM cross_game_concepts WHERE category = ? ORDER BY concept_id", (category,))
        else:
            cur = conn.execute("SELECT * FROM cross_game_concepts ORDER BY category, concept_id")
        results = []
        for r in cur.fetchall():
            results.append({
                "concept_id": r["concept_id"],
                "category": r["category"],
                "name": r["name"],
                "description": r["description"],
                "default_action": r["default_action"],
                "default_params": json.loads(r["default_params"] or "{}"),
                "typical_effects": json.loads(r["typical_effects"] or "{}"),
                "created_at": r["created_at"]
            })
        return results

    def register_cross_game_concept(
        self,
        concept_id: str,
        category: str,
        name: str,
        description: str,
        default_action: str,
        default_params: Dict[str, Any],
        typical_effects: Optional[Dict[str, Any]] = None
    ) -> bool:
        conn = self.db.get_connection()
        now = time.time()
        with conn:
            conn.execute("""
                INSERT INTO cross_game_concepts (concept_id, category, name, description, default_action, default_params, typical_effects, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(concept_id) DO UPDATE SET
                    category = excluded.category,
                    name = excluded.name,
                    description = excluded.description,
                    default_action = excluded.default_action,
                    default_params = excluded.default_params,
                    typical_effects = excluded.typical_effects
            """, (
                concept_id, category, name, description, default_action,
                json.dumps(default_params), json.dumps(typical_effects or {}), now
            ))
        return True

    # ---------------------------------------------------------
    # V2 Gradual Skill Optimization
    # ---------------------------------------------------------
    @staticmethod
    def _skill_candidate_record(row) -> SkillOptimizationCandidate:
        values = dict(row)
        for field in ("preconditions", "steps", "post_verification", "archived_baseline"):
            if values[field] is not None:
                values[field] = json.loads(values[field])
        return SkillOptimizationCandidate(**values)

    @staticmethod
    def _find_skill_optimization_candidate(conn, game_id: str, skill_id: str, candidate_id: str):
        return conn.execute("""
            SELECT c.*, (SELECT COUNT(*) FROM skill_optimization_trials t
                          WHERE t.candidate_id = c.candidate_id) AS trial_count
            FROM skill_optimization_candidates c
            WHERE game_id = ? AND skill_id = ? AND candidate_id = ?
        """, (game_id, skill_id, candidate_id)).fetchone()

    def get_skill_optimization_candidate(
        self, game_id: str, skill_id: str, candidate_id: str,
    ) -> Optional[SkillOptimizationCandidate]:
        row = self._find_skill_optimization_candidate(
            self.db.get_connection(), game_id, skill_id, candidate_id,
        )
        return self._skill_candidate_record(row) if row else None

    def list_skill_optimization_candidates(self, game_id: str, skill_id: str) -> List[SkillOptimizationCandidate]:
        rows = self.db.get_connection().execute("""
            SELECT c.*, (SELECT COUNT(*) FROM skill_optimization_trials t
                          WHERE t.candidate_id = c.candidate_id) AS trial_count
            FROM skill_optimization_candidates c
            WHERE game_id = ? AND skill_id = ? ORDER BY created_at DESC, candidate_id
        """, (game_id, skill_id))
        return [self._skill_candidate_record(row) for row in rows]

    def stage_skill_optimization(
        self,
        skill_id: str,
        new_steps: List[Dict[str, Any]],
        rationale: str = "",
        new_version: Optional[str] = None,
        *,
        game_id: Optional[str] = None,
    ) -> Optional[SkillOptimizationCandidate]:
        """Register an immutable, unverified revision without changing the active row."""
        if not isinstance(new_steps, list) or not new_steps or not all(isinstance(s, dict) for s in new_steps):
            raise ValueError("Optimization requires a non-empty list of steps")
        steps_json = canonical_json(new_steps)
        conn = self.db.get_connection()
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self._find_skill_row(conn, skill_id, game_id)
            if row is None or row["status"] == "rejected" or canonical_json(json.loads(row["steps"])) == steps_json:
                return None
            game_id = row["game_id"]
            baseline_fingerprint = definition_fingerprint(row)
            existing = conn.execute("""
                SELECT version, promoted_at, baseline_fingerprint FROM skill_optimization_candidates
                WHERE game_id = ? AND skill_id = ?
            """, (game_id, skill_id)).fetchall()
            if sum(c["promoted_at"] is None and c["baseline_fingerprint"] == baseline_fingerprint for c in existing) >= 16:
                raise ValueError("Too many pending optimization revisions for this skill")
            version = next_candidate_version(row["version"], {c["version"] for c in existing}, new_version)
            candidate_id = uuid4().hex
            conn.execute("""
                INSERT INTO skill_optimization_candidates (
                    candidate_id, game_id, skill_id, version, base_version, baseline_fingerprint,
                    candidate_fingerprint, name, preconditions, steps, post_verification, rationale, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                candidate_id, game_id, skill_id, version, row["version"], baseline_fingerprint,
                definition_fingerprint(row, version=version, steps=new_steps), row["name"],
                row["preconditions"] or "{}", steps_json, row["post_verification"] or "{}", rationale, time.time(),
            ))
            return self._skill_candidate_record(
                self._find_skill_optimization_candidate(conn, game_id, skill_id, candidate_id)
            )

    def optimize_learned_skill(
        self,
        skill_id: str,
        new_steps: List[Dict[str, Any]],
        rationale: str = "",
        new_version: Optional[str] = None,
        *,
        game_id: Optional[str] = None,
    ) -> bool:
        """Compatibility wrapper: optimize now proposes a candidate, never promotes it."""
        return self.stage_skill_optimization(skill_id, new_steps, rationale, new_version, game_id=game_id) is not None

    @staticmethod
    def _optimization_baseline_matches(active, proposal) -> bool:
        return bool(
            active and active["status"] == "verified"
            and active["source"] not in {"synthetic_fixture", "simulated"}
            and json.loads(active["post_verification"] or "{}")
            and definition_fingerprint(active) == proposal["baseline_fingerprint"]
        )

    def record_skill_benchmark_pair(
        self,
        game_id: str,
        skill_id: str,
        candidate_id: str,
        *,
        baseline: SkillBenchmarkSample,
        candidate: SkillBenchmarkSample,
        baseline_first: bool,
    ) -> bool:
        """Record one matched pair from trusted instrumentation, never from an MCP caller."""
        if self.db.simulation:
            return False
        conn = self.db.get_connection()
        try:
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                proposal = self._find_skill_optimization_candidate(conn, game_id, skill_id, candidate_id)
                if proposal is None or proposal["promoted_at"] is not None or proposal["trial_count"] >= MAX_BENCHMARK_PAIRS:
                    return False
                active = self._find_skill_row(conn, skill_id, game_id)
                if not self._optimization_baseline_matches(active, proposal) or not valid_benchmark_pair(
                    baseline, candidate, baseline_first=baseline_first,
                    baseline_fingerprint=proposal["baseline_fingerprint"],
                    candidate_fingerprint=proposal["candidate_fingerprint"],
                    created_at=proposal["created_at"], now=time.time(),
                ):
                    return False
                latest = conn.execute("""
                    SELECT MAX(finished_at) FROM skill_optimization_trials WHERE candidate_id = ?
                """, (candidate_id,)).fetchone()[0]
                if latest is not None and min(baseline.started_at, candidate.started_at) < latest:
                    return False
                cur = conn.execute("""
                    INSERT INTO skill_optimization_trials (
                        candidate_id, baseline_first, candidate_passed, baseline_latency_ms,
                        candidate_latency_ms, finished_at, evidence
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    candidate_id, baseline_first, candidate.postcondition_passed, baseline.latency_ms,
                    candidate.latency_ms, max(baseline.finished_at, candidate.finished_at),
                    canonical_json({"baseline": asdict(baseline), "candidate": asdict(candidate)}),
                ))
                conn.executemany(
                    "INSERT INTO skill_optimization_capture_ids (capture_id, trial_id) VALUES (?, ?)",
                    [(capture_id, cur.lastrowid) for capture_id in capture_ids(baseline, candidate)],
                )
        except sqlite3.IntegrityError:
            # A capture ID used in any other trial cannot be replayed. The
            # tentative trial row and each earlier capture insert roll back too.
            return False
        return True

    def promote_skill_optimization(self, game_id: str, skill_id: str, candidate_id: str) -> Dict[str, Any]:
        """Commit a candidate only after its own fair, repeated live verification."""
        result = {"success": False, "promoted": False, "candidate_id": candidate_id, "trial_count": 0}
        if self.db.simulation:
            return {**result, "reason": "simulation_has_no_live_evidence"}

        conn = self.db.get_connection()
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            proposal = self._find_skill_optimization_candidate(conn, game_id, skill_id, candidate_id)
            if proposal is None:
                return {**result, "reason": "candidate_not_found"}
            result["trial_count"] = proposal["trial_count"]
            if proposal["promoted_at"] is not None:
                return {**result, "reason": "already_promoted"}
            active = self._find_skill_row(conn, skill_id, game_id)
            if not self._optimization_baseline_matches(active, proposal):
                return {**result, "reason": "baseline_changed_or_not_verified"}
            trials = conn.execute("""
                SELECT baseline_first, candidate_passed, baseline_latency_ms, candidate_latency_ms
                FROM skill_optimization_trials WHERE candidate_id = ?
            """, (candidate_id,)).fetchall()
            if any(not t["candidate_passed"] for t in trials):
                return {**result, "reason": "candidate_postcondition_failed"}
            if len(trials) < MIN_BENCHMARK_PAIRS:
                return {**result, "reason": "insufficient_live_benchmark_pairs"}
            by_order = {0: [], 1: []}
            for trial in trials:
                baseline_ms = trial["baseline_latency_ms"]
                speedup = (baseline_ms - trial["candidate_latency_ms"]) / baseline_ms
                by_order[trial["baseline_first"]].append(speedup)
            if not all(by_order.values()) or abs(len(by_order[0]) - len(by_order[1])) > 1:
                return {**result, "reason": "benchmark_order_not_balanced"}
            # The reported estimate is paired; both execution orders must
            # independently support it to avoid warm-cache/order bias.
            improvement = min(median(order) for order in by_order.values())
            result["median_speedup"] = improvement
            if improvement < MIN_MEDIAN_SPEEDUP:
                return {**result, "reason": "insufficient_speedup"}

            archived = dict(active)
            for field in ("preconditions", "steps", "post_verification"):
                if archived[field] is not None:
                    archived[field] = json.loads(archived[field])
            now = time.time()
            conn.execute("""
                UPDATE learned_skills
                SET steps = ?, version = ?, optimization_notes = ?, source = 'verified_optimization',
                    validation_count = ?, success_count = ?, fail_count = 0, confidence = 1.0,
                    consecutive_failures = 0, updated_at = ?
                WHERE game_id = ? AND skill_id = ?
            """, (
                proposal["steps"], proposal["version"], proposal["rationale"], len(trials), len(trials),
                now, game_id, skill_id,
            ))
            conn.execute("""
                UPDATE skill_optimization_candidates SET promoted_at = ?, archived_baseline = ?
                WHERE candidate_id = ?
            """, (now, canonical_json(archived), candidate_id))
            return {**result, "success": True, "promoted": True, "reason": "promoted", "version": proposal["version"]}

    # ---------------------------------------------------------
    # V2 Temporary Data Pruning
    # ---------------------------------------------------------
    def prune_history(self, game_id: Optional[str] = None, max_records: int = 1000) -> int:
        conn = self.db.get_connection()
        g_filter = "WHERE game_id = ?" if game_id else ""
        params = (game_id,) if game_id else ()
        cur = conn.execute(f"SELECT COUNT(*) FROM action_history {g_filter}", params)
        total = cur.fetchone()[0]
        if total <= max_records:
            return 0
        to_delete = total - max_records
        with conn:
            cur = conn.execute(f"""
                DELETE FROM action_history WHERE id IN (
                    SELECT id FROM action_history {g_filter} ORDER BY created_at ASC LIMIT ?
                )
            """, (*params, to_delete))
            deleted = cur.rowcount
            logger.info("Pruned %d action history entries exceeding ceiling of %d.", deleted, max_records)
            return deleted

    def prune_temporary_screenshots(self, directory: Optional[str] = None, max_age_seconds: int = 3600) -> int:
        target_dirs = []
        if directory:
            if not isinstance(directory, str) or not directory.strip():
                raise ValueError("directory must be a non-empty string.")
            
            # Security Sandbox Validation: Ensure candidate directory resides inside workspace or allowed tmp
            workspace_root = os.path.realpath(os.path.abspath(os.getcwd()))
            tmp_root = os.path.realpath(os.path.abspath(tempfile.gettempdir()))
            candidate = os.path.realpath(os.path.abspath(directory))
            try:
                in_workspace = os.path.commonpath([workspace_root, candidate]) == workspace_root
            except ValueError:
                in_workspace = False
            try:
                in_tmp = os.path.commonpath([tmp_root, candidate]) == tmp_root
            except ValueError:
                in_tmp = False

            if not (in_workspace or in_tmp):
                raise SafetyViolationError(
                    f"Path traversal detected: directory '{directory}' escapes allowed workspace sandbox."
                )
            target_dirs.append(candidate)
        else:
            target_dirs.extend(["data/captures", "storage/screenshots"])
            if os.environ.get("UGM_CAPTURES_DIR"):
                target_dirs.append(os.environ["UGM_CAPTURES_DIR"])

        now = time.time()
        deleted = 0
        for d in target_dirs:
            if not os.path.exists(d):
                continue
            try:
                for fname in os.listdir(d):
                    if fname.endswith((".png", ".jpg", ".jpeg")):
                        fpath = os.path.join(d, fname)
                        if os.path.isfile(fpath):
                            mtime = os.path.getmtime(fpath)
                            if now - mtime > max_age_seconds:
                                try:
                                    os.remove(fpath)
                                    deleted += 1
                                except Exception:
                                    pass
            except Exception as e:
                logger.debug("Screenshot prune notice for %s: %s", d, e)
        return deleted
