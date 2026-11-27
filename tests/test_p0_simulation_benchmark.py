"""Synthetic benchmark trials never count as live optimization evidence."""

from dataclasses import asdict

import pytest

from ugm.simulation_benchmark import (
    SimAction, SimArm, SimClock, SimPairBenchmark, SimState, SimulationBenchmarkError,
)


def make_benchmark(**overrides):
    defaults = dict(
        initial=SimState(),
        expected=SimState(gold=450, wood=280, farms=1, soldiers=1),
        baseline=(SimAction.WAIT, SimAction.BUILD_FARM, SimAction.TRAIN_SOLDIER, SimAction.WAIT),
        candidate=(SimAction.BUILD_FARM, SimAction.TRAIN_SOLDIER),
        clock=SimClock(),
    )
    return SimPairBenchmark(**{**defaults, **overrides})


def complete(bench, order=(SimArm.BASELINE, SimArm.CANDIDATE)):
    for arm in order:
        bench.run_arm(bench.rearm(arm, SimState()))
    return bench.receipt()


@pytest.mark.parametrize("first", [SimArm.BASELINE, SimArm.CANDIDATE])
def test_both_orders_make_equal_deterministic_synthetic_comparisons(first):
    second = SimArm.CANDIDATE if first is SimArm.BASELINE else SimArm.BASELINE
    report = complete(make_benchmark(first=first), (first, second))
    assert report.first is first
    assert report.baseline.elapsed_ms == 170
    assert report.candidate.elapsed_ms == 120
    assert report.baseline.final_state == report.candidate.final_state
    assert report.comparable
    assert report.speedup_percent == 29.412
    assert report.evidence_level == "simulated"
    assert not report.promotion_eligible
    assert asdict(report)["baseline"]["initial_state"] == asdict(SimState())


def test_each_arm_needs_a_new_explicit_local_rearm_and_original_initial_state():
    bench = make_benchmark()
    with pytest.raises(SimulationBenchmarkError, match="expected baseline"):
        bench.rearm(SimArm.CANDIDATE, SimState())
    with pytest.raises(SimulationBenchmarkError, match="initial state"):
        bench.rearm(SimArm.BASELINE, SimState(mana=1))
    first = bench.rearm(SimArm.BASELINE, SimState())
    with pytest.raises(SimulationBenchmarkError, match="already armed"):
        bench.rearm(SimArm.BASELINE, SimState())
    bench.run_arm(first)
    with pytest.raises(SimulationBenchmarkError, match="receipt.*incomplete"):
        bench.receipt()
    with pytest.raises(SimulationBenchmarkError, match="ticket"):
        bench.run_arm(first)
    with pytest.raises(SimulationBenchmarkError, match="initial state"):
        bench.rearm(SimArm.CANDIDATE, SimState(gold=450, wood=280, farms=1, soldiers=1))
    second = bench.rearm(SimArm.CANDIDATE, SimState())
    with pytest.raises(SimulationBenchmarkError, match="ticket"):
        bench.run_arm(first)
    bench.run_arm(second)
    assert bench.receipt().comparable


def test_foreign_tickets_and_arm_results_cannot_be_mixed():
    a, b = make_benchmark(), make_benchmark()
    token_a = a.rearm(SimArm.BASELINE, SimState())
    token_b = b.rearm(SimArm.BASELINE, SimState())
    with pytest.raises(SimulationBenchmarkError, match="ticket"):
        b.run_arm(token_a)
    result_a = a.run_arm(token_a)
    with pytest.raises(SimulationBenchmarkError, match="ticket"):
        b.run_arm(result_a)
    assert b.run_arm(token_b).goal_met


@pytest.mark.parametrize("case", ["expires_while_armed", "cannot_finish", "past_pair_deadline"])
def test_deadline_invalidates_whole_comparison(case):
    clock = SimClock()
    timeout = 100 if case == "cannot_finish" else 200
    bench = make_benchmark(clock=clock, arm_timeout_ms=timeout, pair_timeout_ms=400)
    first = bench.rearm(SimArm.BASELINE, SimState())
    if case == "expires_while_armed":
        clock.advance(200)
        with pytest.raises(SimulationBenchmarkError, match="deadline"):
            bench.run_arm(first)
    elif case == "cannot_finish":
        with pytest.raises(SimulationBenchmarkError, match="deadline"):
            bench.run_arm(first)
        assert clock.now_ms < 100
    else:
        bench.run_arm(first)
        clock.advance(400)
        with pytest.raises(SimulationBenchmarkError, match="deadline"):
            bench.rearm(SimArm.CANDIDATE, SimState())
    with pytest.raises(SimulationBenchmarkError, match="deadline"):
        bench.receipt()


def test_abort_invalidates_pending_ticket_and_cannot_be_rearmed():
    bench = make_benchmark()
    first = bench.rearm(SimArm.BASELINE, SimState())
    bench.abort()
    for action in (lambda: bench.run_arm(first), lambda: bench.rearm(SimArm.BASELINE, SimState()), bench.receipt):
        with pytest.raises(SimulationBenchmarkError, match="aborted"):
            action()


def test_faster_wrong_or_rejected_program_is_not_comparable():
    for bad, status in (
        ((SimAction.BUILD_FARM,), "postcondition_failed"),
        ((SimAction.TOGGLE_PAUSE, SimAction.BUILD_FARM), "action_rejected"),
    ):
        report = complete(make_benchmark(candidate=bad))
        assert report.baseline.goal_met
        assert not report.candidate.goal_met
        assert report.candidate.status == status
        assert not report.comparable
        assert report.speedup_percent is None
        assert not report.promotion_eligible


def test_no_op_cannot_claim_a_behavioral_match():
    with pytest.raises(ValueError, match="postcondition state must differ"):
        make_benchmark(expected=SimState())
    report = complete(make_benchmark(candidate=(SimAction.WAIT,)))
    assert not report.comparable
    assert report.candidate.status == "postcondition_failed"


def test_two_concurrent_uses_of_same_ticket_complete_only_once():
    from concurrent.futures import ThreadPoolExecutor

    bench = make_benchmark()
    ticket = bench.rearm(SimArm.BASELINE, SimState())

    def attempt():
        try:
            return bench.run_arm(ticket).goal_met
        except SimulationBenchmarkError:
            return False

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(lambda _: attempt(), range(2))) == [False, True]


def test_simulator_import_does_not_load_live_execution_modules():
    import subprocess
    import sys

    subprocess.run(
        [sys.executable, "-c", """
import sys
from ugm.simulation_benchmark import SimPairBenchmark
assert all(
    module not in sys.modules
    for module in ('ugm.server', 'ugm.core.capture', 'ugm.core.input', 'ugm.memory.storage',
                   'cv2', 'tkinter', 'mcp', 'pydirectinput', 'sqlite3')
)
"""],
        check=True,
        timeout=10,
    )
