"""An optimized program must not inherit a different program's validation."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from itertools import count
import time

import pytest

from ugm.memory.database import DatabaseManager
from ugm.memory.storage import PersistentKnowledgeStorage


OLD = [{"action": "press_key", "params": {"key": "space"}, "wait_after_s": 0.8}]
FAST = [{"action": "press_key", "params": {"key": "space"}, "wait_after_s": 0.1}]
OTHER = [{"action": "press_key", "params": {"key": "space"}, "wait_after_s": 0.2}]
POST = {"expected_text": "PAUSED"}


@pytest.fixture
def storage(tmp_path):
    db = DatabaseManager(str(tmp_path / "opt.db"))
    try:
        yield PersistentKnowledgeStorage(db)
    finally:
        db.close()


def _learn(storage, game="game", status="verified"):
    storage.save_learned_skill("pause", game, "Pause", OLD, status=status, post_verification=POST)
    return storage.get_learned_skill("pause", game_id=game)


def test_optimize_preserves_existing_active_record(storage):
    active = _learn(storage)
    assert storage.optimize_learned_skill("pause", FAST, "reduce wait", game_id="game")
    assert storage.get_learned_skill("pause", game_id="game") == active


def test_learning_resave_cannot_overwrite_a_verified_program(storage):
    active = _learn(storage)
    assert not storage.save_learned_skill("pause", "game", "Rewrite", OTHER, post_verification={})
    assert storage.get_learned_skill("pause", game_id="game") == active
    assert not storage.save_learned_skill("pause", "game", "Rewrite", OTHER, status="verified", post_verification={})
    assert storage.get_learned_skill("pause", game_id="game") == active
    assert storage.save_learned_skill("pause", "game", "No-op retry", OLD, post_verification=POST)
    assert storage.get_learned_skill("pause", game_id="game") == active


def _stage(storage, game="game", steps=FAST):
    version = storage.stage_skill_optimization("pause", steps, "quicker", game_id=game)
    assert version is not None
    return version


_capture = count()


def _pair(revision, *, baseline_first=True, baseline_ms=100, candidate_ms=70):
    from ugm.memory.skill_optimization import SkillBenchmarkSample

    start = time.time()
    time.sleep(0.003)
    def sample(fingerprint, latency, first):
        n = next(_capture)
        return SkillBenchmarkSample(
            pre_capture_id=f"pre-{n}",
            post_capture_id=f"post-{n}",
            context_key="trusted-game-window-params-and-screen",
            definition_fingerprint=fingerprint,
            latency_ms=latency,
            postcondition_checked=True,
            postcondition_passed=True,
            dry_run=False,
            started_at=start if first else start + 0.001,
            finished_at=start + 0.0005 if first else start + 0.0015,
        )
    return (sample(revision.baseline_fingerprint, baseline_ms, baseline_first),
            sample(revision.candidate_fingerprint, candidate_ms, not baseline_first))


def _record(storage, revision, pair, baseline_first=True):
    return storage.record_skill_benchmark_pair(
        revision.game_id, revision.skill_id, revision.candidate_id, baseline=pair[0],
        candidate=pair[1], baseline_first=baseline_first,
    )


def _promote(storage, revision):
    return storage.promote_skill_optimization(revision.game_id, revision.skill_id, revision.candidate_id)


def test_version_is_immutable_scoped_and_survives_reopen(storage):
    original = _learn(storage)
    _learn(storage, "other")
    revision = _stage(storage)
    sibling = _stage(storage, steps=OTHER)
    assert revision.version != sibling.version
    assert revision.trial_count == 0
    assert storage.get_skill_optimization_candidate("other", "pause", revision.candidate_id) is None
    storage.db.close()
    assert storage.get_skill_optimization_candidate("game", "pause", revision.candidate_id) == revision
    assert storage.get_learned_skill("pause", game_id="game") == original


@pytest.mark.parametrize("change", [
    {"dry_run": True}, {"postcondition_checked": False}, {"context_key": "different-screen"},
    {"definition_fingerprint": "wrong-revision"}, {"latency_ms": float("nan")},
    {"started_at": 1.0}, {"finished_at": 1.0},
])
def test_unverified_simulated_unfair_or_stale_trial_does_not_count(storage, change):
    original = _learn(storage)
    revision = _stage(storage)
    baseline, candidate = _pair(revision)
    assert not _record(storage, revision, (baseline, replace(candidate, **change)))
    current = storage.get_skill_optimization_candidate("game", "pause", revision.candidate_id)
    assert current.trial_count == 0
    assert storage.get_learned_skill("pause", game_id="game") == original


def test_promotion_requires_distinct_order_balanced_trials_and_archives_baseline(storage):
    original = _learn(storage)
    revision = _stage(storage)
    first = _pair(revision)
    assert _record(storage, revision, first)
    assert not _record(storage, revision, first)
    assert not _promote(storage, revision)["promoted"]
    assert _record(storage, revision, _pair(revision))
    assert _record(storage, revision, _pair(revision))
    assert not _promote(storage, revision)["promoted"]
    assert _record(storage, revision, _pair(revision, baseline_first=False), baseline_first=False)
    assert not _promote(storage, revision)["promoted"]  # still 3-to-1 order bias
    assert _record(storage, revision, _pair(revision, baseline_first=False), baseline_first=False)
    result = _promote(storage, revision)
    assert result["promoted"] is True
    active = storage.get_learned_skill("pause", game_id="game")
    assert active.steps == FAST and active.version == revision.version
    assert (active.validation_count, active.success_count, active.fail_count) == (5, 5, 0)
    archive = storage.get_skill_optimization_candidate("game", "pause", revision.candidate_id)
    assert archive.promoted_at is not None and archive.archived_baseline["steps"] == original.steps
    assert not storage.update_skill_stats(
        "pause", False, game_id="game", expected_version=original.version,
    )["updated"]
    assert not _promote(storage, revision)["promoted"]


@pytest.mark.parametrize("failed,slow", [(True, False), (False, True)])
def test_failed_candidate_or_no_material_speedup_cannot_promote(storage, failed, slow):
    original = _learn(storage)
    revision = _stage(storage)
    for baseline_first in (True, False, True):
        baseline, candidate = _pair(revision, baseline_first=baseline_first, candidate_ms=95 if slow else 70)
        if failed:
            candidate = replace(candidate, postcondition_passed=False)
        assert _record(storage, revision, (baseline, candidate), baseline_first=baseline_first)
    assert not _promote(storage, revision)["promoted"]
    assert storage.get_learned_skill("pause", game_id="game") == original


def test_stale_sibling_and_checkpoint_rollback_cannot_reuse_candidate_evidence(storage):
    _learn(storage)
    _learn(storage, "other")
    checkpoint = storage.create_checkpoint("game", "before stage")
    first = _stage(storage)
    sibling = _stage(storage, steps=OTHER)
    second_game = _stage(storage, "other")

    for revision in (first, sibling):
        for order in (True, False, True):
            assert _record(storage, revision, _pair(revision, baseline_first=order), baseline_first=order)
    assert _promote(storage, first)["promoted"]
    assert not _promote(storage, sibling)["promoted"]
    assert storage.rollback_checkpoint("game", checkpoint)
    assert storage.get_skill_optimization_candidate("game", "pause", sibling.candidate_id) is None
    assert storage.get_skill_optimization_candidate("game", "pause", first.candidate_id).promoted_at
    assert storage.get_skill_optimization_candidate("other", "pause", second_game.candidate_id) == second_game
    assert not _promote(storage, first)["promoted"]


def test_concurrent_promotions_have_at_most_one_commit(storage):
    _learn(storage)
    revision = _stage(storage)
    for order in (True, False, True):
        assert _record(storage, revision, _pair(revision, baseline_first=order), baseline_first=order)

    def promote():
        try:
            return _promote(storage, revision)["promoted"]
        finally:
            storage.db.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: promote(), range(2)))
    assert sorted(results) == [False, True]


def test_simulation_db_cannot_benchmark_or_promote(tmp_path):
    db = DatabaseManager(str(tmp_path / "fake.simulation"), simulation=True)
    try:
        storage = PersistentKnowledgeStorage(db)
        _learn(storage)
        revision = _stage(storage)
        assert not _record(storage, revision, _pair(revision))
        assert not _promote(storage, revision)["promoted"]
    finally:
        db.close()


def test_changed_unverified_program_gets_new_version_and_no_previous_evidence(storage):
    original = _learn(storage, status="candidate")
    revision = _stage(storage)
    assert storage.update_skill_stats("pause", True, game_id="game", expected_version=original.version)["updated"]
    assert storage.save_learned_skill("pause", "game", "Edited", OTHER, post_verification=POST)
    edited = storage.get_learned_skill("pause", game_id="game")
    assert edited.version not in {original.version, revision.version}
    assert (edited.validation_count, edited.success_count, edited.fail_count) == (0, 0, 0)
    assert not storage.update_skill_stats("pause", True, game_id="game", expected_version=original.version)["updated"]
    assert storage.get_skill_optimization_candidate("game", "pause", revision.candidate_id) is None


@pytest.mark.parametrize("deletion", ["single", "scoped", "all_skills", "all_memory"])
def test_deleting_and_recreating_skill_cannot_reuse_pending_revision(storage, deletion):
    _learn(storage)
    _learn(storage, "other")
    old = _stage(storage)
    other = _stage(storage, "other")
    assert _record(storage, old, _pair(old))
    if deletion == "single":
        assert storage.delete_learned_skill("pause", game_id="game")
    elif deletion == "scoped":
        storage.delete_memory("game", "skills")
    elif deletion == "all_skills":
        storage.delete_memory(scope="skills")
    else:
        storage.delete_memory()
    _learn(storage)
    assert storage.get_skill_optimization_candidate("game", "pause", old.candidate_id) is None
    assert storage.get_skill_optimization_candidate("other", "pause", other.candidate_id) == (
        other if deletion in {"single", "scoped"} else None
    )
    assert not _promote(storage, old)["promoted"]
    assert _stage(storage).candidate_id != old.candidate_id
