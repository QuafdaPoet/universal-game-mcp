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
import logging
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, asdict

from .database import DatabaseManager, compute_dhash, hamming_distance
from ..skills.contracts import PersistentKnowledgeStorageContract

logger = logging.getLogger("ugm.memory.storage")


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
        success: bool,
        cost: float = 1.0,
        evidence: Optional[Dict[str, Any]] = None,
        source: str = "live"
    ) -> None:
        """Records a verified or failed navigation transition between states."""
        conn = self.db.get_connection()
        now = time.time()
        param_str = json.dumps(params, sort_keys=True)
        ev_str = json.dumps(evidence or {})

        succ_inc = 1 if success else 0
        fail_inc = 0 if success else 1

        with conn:
            conn.execute("""
                INSERT INTO navigation_transitions (
                    game_id, from_state, to_state, action_type, params, cost, success_count, fail_count,
                    confidence, evidence, source, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(game_id, from_state, to_state, action_type, params) DO UPDATE SET
                    success_count = success_count + excluded.success_count,
                    fail_count = fail_count + excluded.fail_count,
                    confidence = CAST(success_count + excluded.success_count AS REAL) / 
                                 CAST(success_count + excluded.success_count + fail_count + excluded.fail_count AS REAL),
                    cost = excluded.cost,
                    evidence = excluded.evidence,
                    updated_at = excluded.updated_at
            """, (
                game_id, from_state, to_state, action_type, param_str, cost,
                succ_inc, fail_inc, 1.0 if success else 0.0, ev_str, source, now, now
            ))

    def get_transitions(
        self,
        game_id: str,
        from_state: Optional[str] = None,
        min_confidence: float = 0.0
    ) -> List[TransitionRecord]:
        """Retrieves verified navigation edges for pathfinding."""
        conn = self.db.get_connection()
        query = "SELECT * FROM navigation_transitions WHERE game_id = ? AND confidence >= ?"
        params: List[Any] = [game_id, min_confidence]
        if from_state:
            query += " AND from_state = ?"
            params.append(from_state)
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
    ) -> None:
        """Saves a candidate or verified learned skill."""
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
        now = time.time()
        steps_json = json.dumps(steps) if isinstance(steps, list) else (steps or "[]")
        init_conf = confidence if confidence is not None else (initial_confidence if initial_confidence is not None else 0.5)

        with conn:
            conn.execute("""
                INSERT INTO learned_skills (
                    skill_id, game_id, version, name, description, preconditions, steps,
                    post_verification, status, validation_count, success_count, fail_count,
                    confidence, source, created_at, updated_at
                ) VALUES (?, ?, '1.0.0', ?, ?, ?, ?, ?, ?, 1, 1, 0, ?, ?, ?, ?)
                ON CONFLICT(skill_id) DO UPDATE SET
                    name = excluded.name,
                    description = excluded.description,
                    preconditions = excluded.preconditions,
                    steps = excluded.steps,
                    post_verification = excluded.post_verification,
                    status = excluded.status,
                    confidence = excluded.confidence,
                    source = excluded.source,
                    updated_at = excluded.updated_at
            """, (
                skill_id, game_id, name, description,
                json.dumps(preconditions or {}), steps_json,
                json.dumps(post_verification or {}), status, init_conf, source, now, now
            ))

    def update_skill_stats(
        self,
        skill_id: str,
        success: bool,
        auto_promote_threshold: int = 3,
        is_execution_error: bool = False,
        reason: str = "",
        latency_ms: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Updates validation stats and manages 4-state lifecycle (candidate, verified, suspect, rejected).
        Critical invariant: One isolated failure must never overwrite previously verified knowledge.
        Distinguishes transient execution errors from true rule/postcondition failures.
        """
        conn = self.db.get_connection()
        now = time.time()
        with conn:
            cur = conn.execute("SELECT validation_count, success_count, fail_count, status, consecutive_failures, confidence FROM learned_skills WHERE skill_id = ?", (skill_id,))
            row = cur.fetchone()
            if not row:
                return {"skill_id": skill_id, "updated": False}

            curr_status = row["status"]
            v_count = row["validation_count"]
            s_count = row["success_count"]
            f_count = row["fail_count"]
            consec_fail = row["consecutive_failures"] if ("consecutive_failures" in row.keys() and row["consecutive_failures"] is not None) else 0

            if is_execution_error:
                # Transient environment / input failure: do NOT penalize rule confidence or change knowledge state!
                f_count += 1
                conn.execute("UPDATE learned_skills SET fail_count = ?, updated_at = ? WHERE skill_id = ?", (f_count, now, skill_id))
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
                consec_fail = 0
            else:
                f_count += 1
                consec_fail += 1

            confidence = round(s_count / max(1, s_count + f_count), 3)
            new_status = curr_status

            if success:
                if curr_status == "candidate" and v_count >= auto_promote_threshold and confidence >= 0.8:
                    new_status = "verified"
                    logger.info("Skill '%s' promoted to VERIFIED after %d successful validations.", skill_id, v_count)
                elif curr_status == "suspect" and s_count >= 2:
                    new_status = "verified"
                    logger.info("Skill '%s' restored to VERIFIED after successful validation recovery.", skill_id)
            else:
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
                WHERE skill_id = ?
            """, (v_count, s_count, f_count, confidence, new_status, consec_fail, now, skill_id))

            return {
                "skill_id": skill_id,
                "status": new_status,
                "previous_status": curr_status,
                "confidence": confidence,
                "validation_count": v_count,
                "consecutive_failures": consec_fail,
                "updated": True
            }

    def get_learned_skill(self, skill_id: str) -> Optional[LearnedSkillRecord]:
        conn = self.db.get_connection()
        cur = conn.execute("SELECT * FROM learned_skills WHERE skill_id = ?", (skill_id,))
        r = cur.fetchone()
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

    def delete_learned_skill(self, skill_id: str) -> bool:
        conn = self.db.get_connection()
        with conn:
            cur = conn.execute("DELETE FROM learned_skills WHERE skill_id = ?", (skill_id,))
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
            if scope in ("all", "states"):
                q = "DELETE FROM ui_states" + (" WHERE game_id = ?" if game_id else "")
                deleted_counts["states"] = conn.execute(q, (game_id,) if game_id else ()).rowcount
                q2 = "DELETE FROM selectors_memory" + (" WHERE game_id = ?" if game_id else "")
                deleted_counts["selectors"] = conn.execute(q2, (game_id,) if game_id else ()).rowcount

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
            self.save_learned_skill(
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
            count += 1
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
        """
        Creates an atomic point-in-time snapshot of all learned knowledge for a game.
        Protects against accidental memory corruption or misleading observations during exploration.
        """
        conn = self.db.get_connection()
        now = time.time()
        states = [asdict(s) for s in self.get_states(game_id)]
        transitions = [asdict(t) for t in self.get_transitions(game_id)]
        skills = [asdict(k) for k in self.list_learned_skills(game_id)]

        cur = conn.execute("SELECT selector_id, state_name, selector_data, confidence, status FROM selectors_memory WHERE game_id = ?", (game_id,))
        selectors = [dict(r) for r in cur.fetchall()]

        snapshot = {
            "game_id": game_id,
            "timestamp": now,
            "label": label,
            "description": description,
            "ui_states": states,
            "transitions": transitions,
            "learned_skills": skills,
            "selectors": selectors
        }
        snap_json = json.dumps(snapshot)

        with conn:
            cur = conn.execute("""
                INSERT INTO checkpoints (game_id, label, description, snapshot_data, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (game_id, label, description, snap_json, now))
            checkpoint_id = cur.lastrowid
            logger.info("Created knowledge checkpoint %d ('%s') for game '%s'.", checkpoint_id, label, game_id)
            return checkpoint_id

    def rollback_checkpoint(self, game_id: str, checkpoint_id: int) -> bool:
        """
        Atomically restores knowledge to a previously saved checkpoint.
        """
        conn = self.db.get_connection()
        cur = conn.execute("SELECT snapshot_data FROM checkpoints WHERE id = ? AND game_id = ?", (checkpoint_id, game_id))
        row = cur.fetchone()
        if not row:
            logger.error("Checkpoint %d for game '%s' not found.", checkpoint_id, game_id)
            return False

        snapshot = json.loads(row["snapshot_data"])
        now = time.time()

        with conn:
            # 1. Clear current knowledge for this game
            conn.execute("DELETE FROM ui_states WHERE game_id = ?", (game_id,))
            conn.execute("DELETE FROM navigation_transitions WHERE game_id = ?", (game_id,))
            conn.execute("DELETE FROM learned_skills WHERE game_id = ?", (game_id,))
            conn.execute("DELETE FROM selectors_memory WHERE game_id = ?", (game_id,))

            # 2. Restore ui_states
            for s in snapshot.get("ui_states", []):
                conn.execute("""
                    INSERT INTO ui_states (game_id, state_name, phash, confidence, evidence, metadata, source, first_seen, last_seen, occurrences)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    s["game_id"], s["state_name"], s["phash"], s["confidence"],
                    json.dumps(s.get("evidence", {})), json.dumps(s.get("metadata", {})),
                    s.get("source", "checkpoint"), s.get("first_seen", now), s.get("last_seen", now), s.get("occurrences", 1)
                ))

            # 3. Restore navigation_transitions
            for t in snapshot.get("transitions", []):
                conn.execute("""
                    INSERT INTO navigation_transitions (
                        game_id, from_state, to_state, action_type, params, cost, success_count, fail_count, confidence, evidence, source, created_at, updated_at, status, consecutive_failures
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    t["game_id"], t["from_state"], t["to_state"], t["action_type"],
                    json.dumps(t["params"]) if isinstance(t["params"], dict) else str(t["params"]),
                    t["cost"], t["success_count"], t["fail_count"], t["confidence"],
                    json.dumps(t.get("evidence", {})), t.get("source", "checkpoint"),
                    t.get("created_at", now), now, t.get("status", "candidate"), t.get("consecutive_failures", 0)
                ))

            # 4. Restore learned_skills
            for sk in snapshot.get("learned_skills", []):
                conn.execute("""
                    INSERT INTO learned_skills (
                        skill_id, game_id, version, name, description, preconditions, steps, post_verification, status, validation_count, success_count, fail_count, confidence, source, created_at, updated_at, consecutive_failures, optimization_notes
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    sk["skill_id"], sk["game_id"], sk.get("version", "1.0.0"), sk["name"], sk.get("description", ""),
                    json.dumps(sk.get("preconditions", {})), json.dumps(sk.get("steps", [])), json.dumps(sk.get("post_verification", {})),
                    sk.get("status", "candidate"), sk.get("validation_count", 0), sk.get("success_count", 0), sk.get("fail_count", 0),
                    sk.get("confidence", 0.5), sk.get("source", "checkpoint"), sk.get("created_at", now), now,
                    sk.get("consecutive_failures", 0), sk.get("optimization_notes", None)
                ))

            # 5. Restore selectors_memory
            for sel in snapshot.get("selectors", []):
                conn.execute("""
                    INSERT INTO selectors_memory (game_id, selector_id, state_name, selector_data, confidence, success_count, fail_count, last_verified, source, status, consecutive_failures)
                    VALUES (?, ?, ?, ?, ?, 1, 0, ?, 'checkpoint', ?, 0)
                """, (
                    game_id, sel["selector_id"], sel.get("state_name", "global"),
                    sel.get("selector_data", "{}") if isinstance(sel.get("selector_data"), str) else json.dumps(sel.get("selector_data", {})),
                    sel.get("confidence", 1.0), now, sel.get("status", "candidate")
                ))

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
                cur = conn.execute("UPDATE learned_skills SET status = ?, updated_at = ? WHERE game_id = ? AND skill_id = ?", (new_status, now, game_id, item_id))
                return cur.rowcount > 0
            elif item_type == "transition":
                cur = conn.execute("UPDATE navigation_transitions SET status = ?, updated_at = ? WHERE game_id = ? AND id = ?", (new_status, now, game_id, int(item_id)))
                return cur.rowcount > 0
            elif item_type == "selector":
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
    def optimize_learned_skill(
        self,
        skill_id: str,
        new_steps: List[Dict[str, Any]],
        rationale: str = "",
        new_version: Optional[str] = None
    ) -> bool:
        conn = self.db.get_connection()
        cur = conn.execute("SELECT version, name FROM learned_skills WHERE skill_id = ?", (skill_id,))
        row = cur.fetchone()
        if not row:
            return False

        old_ver = row["version"] or "1.0.0"
        if new_version:
            ver = new_version
        else:
            # Increment minor version e.g. 1.0.0 -> 1.1.0
            parts = old_ver.split(".")
            if len(parts) >= 2 and parts[1].isdigit():
                parts[1] = str(int(parts[1]) + 1)
                ver = ".".join(parts)
            else:
                ver = f"{old_ver}.1"

        now = time.time()
        with conn:
            conn.execute("""
                UPDATE learned_skills SET
                    steps = ?,
                    version = ?,
                    optimization_notes = ?,
                    consecutive_failures = 0,
                    updated_at = ?
                WHERE skill_id = ?
            """, (json.dumps(new_steps), ver, rationale, now, skill_id))
        logger.info("Optimized skill '%s' to version %s (notes: %s).", skill_id, ver, rationale)
        return True

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
            target_dirs.append(directory)
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
