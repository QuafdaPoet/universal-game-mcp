"""
Universal Game MCP (UGM) - Verified Self-Learning Pipeline
Implements the Observe -> Identify -> Plan -> Act -> Verify -> Learn -> Reuse pipeline.
Records discovered workflows as candidate skills, tracks empirical validation history,
promotes verified skills to reusable status, and protects curated Game Skill packs.
"""

from __future__ import annotations
import time
import logging
from typing import Dict, List, Optional, Any, Tuple, TYPE_CHECKING
from dataclasses import dataclass, asdict

from .engine import SkillExecutionEngine, SkillExecutionResult, ALLOWLISTED_ACTION_PRIMITIVES
from ..core.safety import SafetyViolationError

if TYPE_CHECKING:
    from ..memory.storage import PersistentKnowledgeStorage, LearnedSkillRecord

logger = logging.getLogger("ugm.skills.learner")


class SkillLearner:
    """
    Manages procedural self-learning, validation, and promotion of Game Skills.
    """

    def __init__(
        self,
        storage: PersistentKnowledgeStorage,
        execution_engine: SkillExecutionEngine,
        min_validations_to_promote: int = 2,
        min_confidence_to_promote: float = 0.80
    ):
        self.storage = storage
        self.engine = execution_engine
        self.min_validations = min_validations_to_promote
        self.min_confidence = min_confidence_to_promote

    def learn_candidate_skill(
        self,
        game_id: str,
        skill_id: str,
        name: str,
        steps: List[Dict[str, Any]],
        description: str = "",
        preconditions: Optional[Dict[str, Any]] = None,
        post_verification: Optional[Dict[str, Any]] = None,
        source: str = "live"
    ) -> Dict[str, Any]:
        """
        Saves a new workflow as a candidate reusable skill.
        Curated Game Skills are strictly protected from being overwritten.
        """
        # Invariant 1: Curated Game Skills Protection
        existing = self.engine.lookup_skill(skill_id, game_id=game_id)
        if existing and existing.get("is_curated"):
            raise SafetyViolationError(
                f"Cannot overwrite curated Game Skill '{skill_id}' in game '{game_id}'. "
                f"Curated skills are protected. Choose a different skill_id for learned skills."
            )

        # Invariant 2: Action Primitives Allowlist
        if not steps:
            raise ValueError(f"Cannot learn skill '{skill_id}': step sequence is empty.")

        for idx, step in enumerate(steps):
            act = step.get("action")
            if act not in ALLOWLISTED_ACTION_PRIMITIVES:
                raise SafetyViolationError(
                    f"Step {idx + 1} has disallowed action primitive '{act}'. "
                    f"Allowed primitives: {ALLOWLISTED_ACTION_PRIMITIVES}"
                )

        # Fixture provenance is set by the server, not a caller-supplied label.
        if self.storage.db.simulation:
            source = "synthetic_fixture"
        elif source in {"synthetic_fixture", "simulated", "dry_run"}:
            raise ValueError("Simulated skills cannot be learned into the live knowledge database.")

        # Save to persistent knowledge store as candidate
        saved = self.storage.save_learned_skill(
            skill_id=skill_id,
            game_id=game_id,
            name=name,
            steps=steps,
            description=description,
            preconditions=preconditions or {"window_focused": True},
            post_verification=post_verification or {},
            status="candidate",
            source=source
        )

        if not saved:
            raise ValueError("Existing verified skill is unchanged; use optimize_skill to stage a revision.")
        recorded = self.storage.get_learned_skill(skill_id, game_id=game_id)
        status = recorded.status if recorded else "candidate"
        logger.info("Saved or retained skill '%s' for game '%s' (status: %s).", skill_id, game_id, status)
        return {
            "skill_id": skill_id,
            "game_id": game_id,
            "status": status,
            "message": f"Skill '{skill_id}' recorded or retained as {status}.",
            "steps_count": len(steps),
        }

    def validate_skill(
        self,
        skill_id: str,
        game_id: Optional[str] = None,
        dry_run: bool = False
    ) -> SkillExecutionResult:
        """
        Executes a candidate or learned skill and updates its empirical validation track record.
        Promotes to 'verified' if validation thresholds are reached.
        """
        result = self.engine.execute_skill(
            skill_id=skill_id,
            game_id=game_id,
            dry_run=dry_run
        )

        if result.dry_run or not result.post_verification_passed:
            return result

        # Check live validation stats
        skill_rec = self.storage.get_learned_skill(skill_id, game_id=result.game_id)
        if skill_rec:
            # Check promotion
            if (
                skill_rec.status == "candidate"
                and skill_rec.validation_count >= self.min_validations
                and skill_rec.confidence >= self.min_confidence
            ):
                self.approve_skill(skill_id, game_id=result.game_id)

        return result

    def approve_skill(self, skill_id: str, *, game_id: Optional[str] = None) -> bool:
        """Promotes a candidate skill to 'verified'."""
        skill = self.storage.get_learned_skill(skill_id, game_id=game_id)
        if not skill:
            return False
        return self.storage.update_knowledge_state(
            game_id=skill.game_id,
            item_type="skill",
            item_id=skill_id,
            new_status="verified",
            reason="approved"
        )

    def reject_skill(self, skill_id: str, reason: str = "", *, game_id: Optional[str] = None) -> bool:
        """Marks a candidate skill as 'rejected'."""
        skill = self.storage.get_learned_skill(skill_id, game_id=game_id)
        if not skill:
            return False
        return self.storage.update_knowledge_state(
            game_id=skill.game_id,
            item_type="skill",
            item_id=skill_id,
            new_status="rejected",
            reason=reason
        )

    def mark_skill_suspect(self, skill_id: str, reason: str = "", *, game_id: Optional[str] = None) -> bool:
        """Marks a previously verified skill as 'suspect' following unexpected failures."""
        skill = self.storage.get_learned_skill(skill_id, game_id=game_id)
        if not skill:
            return False
        return self.storage.update_knowledge_state(
            game_id=skill.game_id,
            item_type="skill",
            item_id=skill_id,
            new_status="suspect",
            reason=reason
        )

    def stage_optimization(
        self,
        skill_id: str,
        new_steps: List[Dict[str, Any]],
        rationale: str = "",
        new_version: Optional[str] = None,
        *,
        game_id: Optional[str] = None,
    ):
        """Stages a changed program without replacing the current skill.
        """
        if not new_steps or not isinstance(new_steps, list):
            raise ValueError(f"Cannot optimize skill '{skill_id}': step sequence cannot be empty.")

        learned = self.storage.get_learned_skill(skill_id, game_id=game_id)
        scoped_game_id = game_id if game_id is not None else (learned.game_id if learned else None)
        existing = self.engine.lookup_skill(skill_id, game_id=scoped_game_id)
        if existing and existing.get("is_curated"):
            raise SafetyViolationError(
                f"Cannot modify curated Game Skill '{skill_id}'. Curated skills are protected."
            )

        # Validate action primitives
        for idx, step in enumerate(new_steps):
            if not isinstance(step, dict):
                raise ValueError(f"Optimized step {idx + 1} must be an object.")
            act = step.get("action")
            if act not in ALLOWLISTED_ACTION_PRIMITIVES:
                raise SafetyViolationError(
                    f"Optimized step {idx + 1} has disallowed action primitive '{act}'."
                )
        if learned is None:
            return None
        return self.storage.stage_skill_optimization(
            skill_id=skill_id,
            game_id=learned.game_id,
            new_steps=new_steps,
            rationale=rationale,
            new_version=new_version
        )

    def optimize_skill(
        self,
        skill_id: str,
        new_steps: List[Dict[str, Any]],
        rationale: str = "",
        new_version: Optional[str] = None,
        *,
        game_id: Optional[str] = None,
    ) -> bool:
        """Compatibility wrapper: success means staged, not promoted."""
        return self.stage_optimization(
            skill_id, new_steps, rationale, new_version, game_id=game_id,
        ) is not None

    def delete_skill(self, skill_id: str, *, game_id: Optional[str] = None) -> bool:
        """Deletes a learned skill entry."""
        return self.storage.delete_learned_skill(skill_id, game_id=game_id)

    def list_learned_skills(
        self,
        game_id: Optional[str] = None,
        status: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Lists learned skills matching criteria."""
        records = self.storage.list_learned_skills(game_id=game_id, status=status)
        return [asdict(r) for r in records]

    def get_learning_status(self, game_id: Optional[str] = None) -> Dict[str, Any]:
        """Reports current learning and memory status."""
        stats = self.storage.get_statistics(game_id=game_id)
        skills = self.list_learned_skills(game_id=game_id)
        return {
            **stats,
            "learned_skills_summary": [
                {
                    "skill_id": s["skill_id"],
                    "status": s["status"],
                    "validations": s["validation_count"],
                    "confidence": s["confidence"]
                }
                for s in skills
            ]
        }
