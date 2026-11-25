"""A successful rehearsal must not create evidence in the live knowledge namespace."""
from ugm.memory.database import DatabaseManager
from ugm.memory.storage import PersistentKnowledgeStorage
from ugm.simulation_benchmark import SimAction, SimArm, SimClock, SimPairBenchmark, SimState


def test_successful_synthetic_pair_has_no_live_promotion_credit(tmp_path):
    bench = SimPairBenchmark(
        initial=SimState(), expected=SimState(gold=450, wood=280, farms=1, soldiers=1),
        baseline=(SimAction.WAIT, SimAction.BUILD_FARM, SimAction.TRAIN_SOLDIER),
        candidate=(SimAction.BUILD_FARM, SimAction.TRAIN_SOLDIER), clock=SimClock(),
        first=SimArm.BASELINE,
    )
    for arm in (SimArm.BASELINE, SimArm.CANDIDATE):
        bench.run_arm(bench.rearm(arm, SimState()))
    report = bench.receipt()
    assert report.comparable and not report.promotion_eligible

    db = DatabaseManager(tmp_path / "isolated-knowledge.db")
    try:
        store = PersistentKnowledgeStorage(db)
        store.save_learned_skill(
            game_id="offline_demo", skill_id="build", name="Build", status="verified",
            steps=[{"action": "press_keys", "params": {"keys": ["b"]}}],
            post_verification={"expected_text": "Built"},
        )
        candidate = store.stage_skill_optimization(
            "build", [{"action": "press_keys", "params": {"keys": ["space"]}}], game_id="offline_demo",
        )
        assert candidate is not None and not db.simulation
        assert not store.record_skill_benchmark_pair(
            "offline_demo", "build", candidate.candidate_id,
            baseline=report.baseline, candidate=report.candidate, baseline_first=True,
        )
        outcome = store.promote_skill_optimization("offline_demo", "build", candidate.candidate_id)
        assert outcome["reason"] == "insufficient_live_benchmark_pairs"
        assert not outcome["promoted"] and outcome["trial_count"] == 0
        assert store.get_learned_skill("build", game_id="offline_demo").version == "1.0.0"
    finally:
        db.close()
