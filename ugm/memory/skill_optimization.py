"""Internal version-scoped skill proposal and benchmark contracts."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import math
import re
from typing import Any


MIN_BENCHMARK_PAIRS = 3
MAX_BENCHMARK_PAIRS = 20
MIN_MEDIAN_SPEEDUP = 0.10
_MAX_PAIR_AGE_SECONDS = 120.0
_MAX_PAIR_SEPARATION_SECONDS = 60.0


@dataclass(frozen=True)
class SkillOptimizationCandidate:
    candidate_id: str
    game_id: str
    skill_id: str
    version: str
    base_version: str
    baseline_fingerprint: str
    candidate_fingerprint: str
    name: str
    preconditions: dict[str, Any]
    steps: list[dict[str, Any]]
    post_verification: dict[str, Any]
    rationale: str
    created_at: float
    promoted_at: float | None
    trial_count: int
    archived_baseline: dict[str, Any] | None


@dataclass(frozen=True)
class SkillBenchmarkSample:
    """One internally measured arm, never accepted as caller-supplied MCP data."""

    pre_capture_id: str
    post_capture_id: str
    context_key: str
    definition_fingerprint: str
    latency_ms: float
    postcondition_checked: bool
    postcondition_passed: bool
    dry_run: bool
    started_at: float
    finished_at: float


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def definition_fingerprint(row, *, version=None, steps=None) -> str:
    """Hash behavior and revision identity, not counters changed by normal use."""
    definition = {
        "game_id": row["game_id"],
        "skill_id": row["skill_id"],
        "version": version if version is not None else row["version"],
        "preconditions": json.loads(row["preconditions"] or "{}"),
        "steps": steps if steps is not None else json.loads(row["steps"]),
        "post_verification": json.loads(row["post_verification"] or "{}"),
    }
    return sha256(canonical_json(definition).encode("utf-8")).hexdigest()


def next_candidate_version(active: str, existing: set[str], requested: str | None) -> str:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", active)
    if requested is not None:
        requested_match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", requested)
        if requested in existing or requested == active or not requested_match or (
            match and tuple(map(int, requested_match.groups())) <= tuple(map(int, match.groups()))
        ):
            raise ValueError("Optimization version must be new and greater than the current learned version")
        return requested

    if match:
        major, minor, patch = map(int, match.groups())
        while True:
            minor += 1
            version = f"{major}.{minor}.{patch}"
            if version not in existing:
                return version
    ordinal = 1
    while f"{active}.{ordinal}" in existing:
        ordinal += 1
    return f"{active}.{ordinal}"


def capture_ids(baseline: SkillBenchmarkSample, candidate: SkillBenchmarkSample) -> tuple[str, str, str, str]:
    return baseline.pre_capture_id, baseline.post_capture_id, candidate.pre_capture_id, candidate.post_capture_id


def _finite_positive(value: object) -> bool:
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value) and value > 0
    except OverflowError:
        return False


def valid_benchmark_pair(
    baseline: SkillBenchmarkSample,
    candidate: SkillBenchmarkSample,
    *,
    baseline_first: bool,
    baseline_fingerprint: str,
    candidate_fingerprint: str,
    created_at: float,
    now: float,
) -> bool:
    """Evidence freshness, program identity and comparability are mandatory."""
    if type(baseline_first) is not bool or not isinstance(baseline, SkillBenchmarkSample) or not isinstance(candidate, SkillBenchmarkSample):
        return False
    ids = capture_ids(baseline, candidate)
    if any(type(value) is not str or not value.strip() for value in ids) or len(set(ids)) != 4:
        return False
    if type(baseline.context_key) is not str or not baseline.context_key.strip() or baseline.context_key != candidate.context_key:
        return False
    if baseline.definition_fingerprint != baseline_fingerprint or candidate.definition_fingerprint != candidate_fingerprint:
        return False
    if baseline.postcondition_passed is not True or type(candidate.postcondition_passed) is not bool:
        return False
    for sample in (baseline, candidate):
        if sample.dry_run is not False or sample.postcondition_checked is not True:
            return False
        if not all(_finite_positive(value) for value in (sample.latency_ms, sample.started_at, sample.finished_at)):
            return False
        if not created_at <= sample.started_at < sample.finished_at <= now:
            return False
    first, second = (baseline, candidate) if baseline_first else (candidate, baseline)
    return (
        first.finished_at <= second.started_at
        and second.started_at - first.finished_at <= _MAX_PAIR_SEPARATION_SECONDS
        and now - first.started_at <= _MAX_PAIR_AGE_SECONDS
    )
