"""
Universal Game MCP (UGM) - Persistent Knowledge Storage Engine
Implements PersistentKnowledgeStorageContract with game partitioning,
WAL-backed SQLite persistence, source attribution ('live' vs 'synthetic_fixture'),
bounded history pruning, and export/import.
"""

from __future__ import annotations
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
    status: str  # 'candidate', 'verified', 'rejected'
    validation_count: int
    success_count: int
    fail_count: int
    confidence: float
    source: str
    created_at: float
    updated_at: float


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
                updated_at=r["updated_at"]
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
        skill_id: str,
        game_id: str,
        name: str,
        steps: List[Dict[str, Any]],
        description: str = "",
        preconditions: Optional[Dict[str, Any]] = None,
        post_verification: Optional[Dict[str, Any]] = None,
        status: str = "candidate",
        source: str = "live"
    ) -> None:
        """Saves a candidate or verified learned skill."""
        conn = self.db.get_connection()
        now = time.time()
        with conn:
            conn.execute("""
                INSERT INTO learned_skills (
                    skill_id, game_id, version, name, description, preconditions, steps,
                    post_verification, status, validation_count, success_count, fail_count,
                    confidence, source, created_at, updated_at
                ) VALUES (?, ?, '1.0.0', ?, ?, ?, ?, ?, ?, 1, 1, 0, 0.5, ?, ?, ?)
                ON CONFLICT(skill_id) DO UPDATE SET
                    name = excluded.name,
                    description = excluded.description,
                    preconditions = excluded.preconditions,
                    steps = excluded.steps,
                    post_verification = excluded.post_verification,
                    status = excluded.status,
                    source = excluded.source,
                    updated_at = excluded.updated_at
            """, (
                skill_id, game_id, name, description,
                json.dumps(preconditions or {}), json.dumps(steps),
                json.dumps(post_verification or {}), status, source, now, now
            ))

    def update_skill_stats(
        self,
        skill_id: str,
        success: bool,
        auto_promote_threshold: int = 3
    ) -> None:
        """Updates validation stats and promotes candidate to verified if criteria met."""
        conn = self.db.get_connection()
        now = time.time()
        with conn:
            cur = conn.execute("SELECT validation_count, success_count, fail_count, status FROM learned_skills WHERE skill_id = ?", (skill_id,))
            row = cur.fetchone()
            if not row:
                return

            v_count = row["validation_count"] + 1
            s_count = row["success_count"] + (1 if success else 0)
            f_count = row["fail_count"] + (0 if success else 1)
            confidence = round(s_count / max(1, s_count + f_count), 3)

            new_status = row["status"]
            if new_status == "candidate" and v_count >= auto_promote_threshold and confidence >= 0.8:
                new_status = "verified"
                logger.info("Skill '%s' promoted to VERIFIED after %d successful validations.", skill_id, v_count)

            conn.execute("""
                UPDATE learned_skills SET
                    validation_count = ?,
                    success_count = ?,
                    fail_count = ?,
                    confidence = ?,
                    status = ?,
                    updated_at = ?
                WHERE skill_id = ?
            """, (v_count, s_count, f_count, confidence, new_status, now, skill_id))

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
            updated_at=r["updated_at"]
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
                updated_at=r["updated_at"]
            ))
        return skills

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
