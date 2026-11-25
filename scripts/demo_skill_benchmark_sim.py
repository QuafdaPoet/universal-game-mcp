"""Run a headless, synthetic-only skill benchmark rehearsal.

This local demo never accesses a game window or the persistent knowledge store.
Reported times are simulated costs, not hardware measurements.
"""
from argparse import ArgumentParser
from dataclasses import asdict
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ugm.simulation_benchmark import (  # noqa: E402
    SimAction, SimArm, SimClock, SimPairBenchmark, SimState,
)


def main() -> int:
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--first", choices=("baseline", "candidate"), default="baseline")
    args = parser.parse_args()
    first = SimArm(args.first)
    second = SimArm.CANDIDATE if first == SimArm.BASELINE else SimArm.BASELINE

    bench = SimPairBenchmark(
        initial=SimState(),
        expected=SimState(gold=450, wood=280, farms=1, soldiers=1),
        baseline=(SimAction.WAIT, SimAction.BUILD_FARM, SimAction.TRAIN_SOLDIER, SimAction.WAIT),
        candidate=(SimAction.BUILD_FARM, SimAction.TRAIN_SOLDIER),
        clock=SimClock(),
        first=first,
    )
    arms = []
    for arm in (first, second):
        # Each arm needs independently supplied toy starting state and rearming.
        # The runner never restores a state or launches the other arm itself.
        receipt = bench.run_arm(bench.rearm(arm, SimState()))
        arm_report = asdict(receipt)
        arm_report["simulated_latency_ms"] = arm_report.pop("elapsed_ms")
        arm_report["postcondition_passed"] = arm_report.pop("goal_met")
        arms.append(arm_report)

    pair = bench.receipt()
    print(json.dumps({
        "source": pair.source,
        "evidence_level": pair.evidence_level,
        "promotion_eligible": pair.promotion_eligible,
        "clock": pair.clock,
        "arms": arms,
        "comparable": pair.comparable,
        "simulated_speedup_percent": pair.speedup_percent,
    }, indent=2))
    return 0 if pair.comparable else 1


if __name__ == "__main__":
    raise SystemExit(main())
