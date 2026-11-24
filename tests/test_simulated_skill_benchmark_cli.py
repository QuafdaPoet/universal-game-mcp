"""Exercise the development demo in a separate process with no game or database."""
import json
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "demo_skill_benchmark_sim.py"


@pytest.mark.parametrize("first,second", [("baseline", "candidate"), ("candidate", "baseline")])
def test_demo_keeps_a_b_results_synthetic(tmp_path, first, second):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--first", first],
        cwd=tmp_path, capture_output=True, text=True, timeout=10, check=True,
    )
    report = json.loads(result.stdout)
    assert report["source"] == "synthetic_fixture"
    assert report["promotion_eligible"] is False
    assert report["clock"] == "fake"
    assert [arm["arm"] for arm in report["arms"]] == [first, second]
    by_arm = {arm["arm"]: arm for arm in report["arms"]}
    assert by_arm["baseline"]["simulated_latency_ms"] > by_arm["candidate"]["simulated_latency_ms"] > 0
    assert all(arm["postcondition_passed"] for arm in report["arms"])
    assert not list(tmp_path.iterdir())
