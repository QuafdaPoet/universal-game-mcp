"""Developer-only paired benchmark over a headless, in-memory strategy fixture.

The toy rules mirror the offline demonstration's resource costs and pause
switch. A tick measures modeled work, not elapsed wall time or game performance.
This module has no GUI, MCP, operating system input, capture, or storage access.
"""

from dataclasses import dataclass, field, replace
from enum import Enum
from threading import Event, Lock


_MAX_STEPS = 40
_MAX_TIMEOUT_MS = 60_000


class SimulationBenchmarkError(RuntimeError):
    """An invalid, aborted or expired synthetic benchmark operation."""


class SimArm(str, Enum):
    BASELINE = "baseline"
    CANDIDATE = "candidate"


class SimAction(str, Enum):
    BUILD_FARM = "build_farm"
    TRAIN_SOLDIER = "train_soldier"
    TOGGLE_PAUSE = "toggle_pause"
    WAIT = "wait"


_COST_MS = {
    SimAction.BUILD_FARM: 60,
    SimAction.TRAIN_SOLDIER: 60,
    SimAction.TOGGLE_PAUSE: 30,
    SimAction.WAIT: 25,
}


@dataclass(frozen=True, slots=True)
class SimState:
    gold: int = 500
    wood: int = 320
    mana: int = 100
    farms: int = 0
    soldiers: int = 0
    paused: bool = False

    def __post_init__(self):
        for field in ("gold", "wood", "mana", "farms", "soldiers"):
            value = getattr(self, field)
            if type(value) is not int or value < 0:
                raise ValueError(f"{field} must be a non-negative integer")
        if type(self.paused) is not bool:
            raise ValueError("paused must be a boolean")


class SimClock:
    """Manually advance a monotonic, integer millisecond simulation clock."""

    def __init__(self):
        self._now_ms = 0
        self._lock = Lock()

    @property
    def now_ms(self) -> int:
        with self._lock:
            return self._now_ms

    def advance(self, milliseconds: int) -> None:
        if type(milliseconds) is not int or milliseconds < 0:
            raise ValueError("The fake clock can only advance by non-negative integer milliseconds")
        with self._lock:
            self._now_ms += milliseconds

    def _advance_before(self, milliseconds: int, deadline_ms: int) -> bool:
        """Consume a step's modeled cost atomically only if it fits."""
        with self._lock:
            if self._now_ms >= deadline_ms or self._now_ms + milliseconds > deadline_ms:
                return False
            self._now_ms += milliseconds
            return True


@dataclass(frozen=True, slots=True, eq=False)
class SimArmTicket:
    """An opaque, process-local handle. Reconstructing it does not arm a trial."""

    arm: SimArm


@dataclass(frozen=True, slots=True)
class SimArmReceipt:
    arm: SimArm
    program: tuple[SimAction, ...]
    initial_state: SimState
    final_state: SimState
    started_ms: int
    finished_ms: int
    elapsed_ms: int
    goal_met: bool
    status: str


@dataclass(frozen=True, slots=True)
class SimPairReceipt:
    first: SimArm
    baseline: SimArmReceipt
    candidate: SimArmReceipt
    comparable: bool
    speedup_percent: float | None
    evidence_level: str = field(default="simulated", init=False)
    source: str = field(default="synthetic_fixture", init=False)
    clock: str = field(default="fake", init=False)
    promotion_eligible: bool = field(default=False, init=False)


@dataclass(frozen=True, slots=True)
class _ArmedTrial:
    arm: SimArm
    ticket: SimArmTicket
    initial: SimState
    deadline_ms: int


def _apply(state: SimState, action: SimAction) -> tuple[SimState, bool]:
    if action is SimAction.WAIT:
        return state, True
    if action is SimAction.TOGGLE_PAUSE:
        return replace(state, paused=not state.paused), True
    if action is SimAction.BUILD_FARM and not state.paused and state.gold >= 50:
        return replace(state, gold=state.gold - 50, farms=state.farms + 1), True
    if action is SimAction.TRAIN_SOLDIER and not state.paused and state.wood >= 40:
        return replace(state, wood=state.wood - 40, soldiers=state.soldiers + 1), True
    return state, False


class SimPairBenchmark:
    """Runs at most one explicitly rearmed arm at a time, in the chosen order.

    Comparing results never grants live skill credit. To start another pair,
    construct a new benchmark with fresh state; no reset is performed here.
    """

    def __init__(
        self,
        *,
        initial: SimState,
        expected: SimState,
        baseline: tuple[SimAction, ...],
        candidate: tuple[SimAction, ...],
        clock: SimClock,
        first: SimArm = SimArm.BASELINE,
        arm_timeout_ms: int = 1_000,
        pair_timeout_ms: int = 5_000,
    ):
        if type(initial) is not SimState or type(expected) is not SimState:
            raise ValueError("The initial and expected states must be SimState snapshots")
        if initial == expected:
            raise ValueError("Expected postcondition state must differ from the initial state")
        if type(clock) is not SimClock or type(first) is not SimArm:
            raise ValueError("A SimClock and an explicit SimArm execution order are required")
        if (
            type(arm_timeout_ms) is not int or type(pair_timeout_ms) is not int
            or not 0 < arm_timeout_ms <= pair_timeout_ms <= _MAX_TIMEOUT_MS
        ):
            raise ValueError("Expected 0 < arm timeout <= pair timeout <= 60000 fake milliseconds")
        for program in (baseline, candidate):
            if (
                type(program) is not tuple or not 1 <= len(program) <= _MAX_STEPS
                or any(type(step) is not SimAction for step in program)
            ):
                raise ValueError("Benchmark programs must contain 1–40 typed simulator actions")

        self._initial = initial
        self._expected = expected
        self._programs = {SimArm.BASELINE: baseline, SimArm.CANDIDATE: candidate}
        self._clock = clock
        other = SimArm.CANDIDATE if first is SimArm.BASELINE else SimArm.BASELINE
        self._order = (first, other)
        self._arm_timeout_ms = arm_timeout_ms
        self._pair_timeout_ms = pair_timeout_ms
        self._pair_deadline_ms = None
        self._armed: _ArmedTrial | None = None
        self._results: dict[SimArm, SimArmReceipt] = {}
        self._failure = None
        self._aborted = Event()
        self._lock = Lock()

    def _ensure_usable(self) -> None:
        if self._aborted.is_set():
            raise SimulationBenchmarkError("Synthetic benchmark aborted")
        if self._failure is not None:
            raise SimulationBenchmarkError(self._failure)

    def _deadline_failure(self) -> None:
        self._armed = None
        self._failure = "Synthetic benchmark deadline exceeded"
        raise SimulationBenchmarkError(self._failure)

    def rearm(self, arm: SimArm, initial: SimState) -> SimArmTicket:
        """Authorize one specific arm with a matching local simulator snapshot."""
        with self._lock:
            self._ensure_usable()
            if len(self._results) == 2:
                raise SimulationBenchmarkError("Synthetic benchmark already complete")
            now = self._clock.now_ms
            if (
                (self._pair_deadline_ms is not None and now >= self._pair_deadline_ms)
                or (self._armed is not None and now >= self._armed.deadline_ms)
            ):
                self._deadline_failure()
            if self._armed is not None:
                raise SimulationBenchmarkError("A synthetic arm is already armed")
            next_arm = self._order[len(self._results)]
            if arm is not next_arm:
                raise SimulationBenchmarkError(f"Synthetic benchmark expected {next_arm.value} next")
            if type(initial) is not SimState or initial != self._initial:
                raise SimulationBenchmarkError("Arm rejected: initial state does not match the scenario")
            if self._pair_deadline_ms is None:
                self._pair_deadline_ms = now + self._pair_timeout_ms
            ticket = SimArmTicket(arm)
            self._armed = _ArmedTrial(arm, ticket, initial, min(
                now + self._arm_timeout_ms, self._pair_deadline_ms,
            ))
            return ticket

    def run_arm(self, ticket: SimArmTicket) -> SimArmReceipt:
        """Consume the one-use ticket and execute only its bound toy program."""
        with self._lock:
            self._ensure_usable()
            armed = self._armed
            if armed is None or ticket is not armed.ticket:
                raise SimulationBenchmarkError("No matching unused arm ticket for this benchmark")
            self._armed = None
            started = self._clock.now_ms
            if started >= armed.deadline_ms:
                self._deadline_failure()

            state = armed.initial
            status = "completed"
            elapsed = 0
            program = self._programs[armed.arm]
            for step in program:
                self._ensure_usable()
                cost = _COST_MS[step]
                if not self._clock._advance_before(cost, armed.deadline_ms):
                    self._deadline_failure()
                elapsed += cost
                state, accepted = _apply(state, step)
                if not accepted:
                    status = "action_rejected"
                    break

            self._ensure_usable()
            finished = self._clock.now_ms
            if finished > armed.deadline_ms:
                self._deadline_failure()
            goal_met = status == "completed" and state == self._expected
            if status == "completed" and not goal_met:
                status = "postcondition_failed"
            result = SimArmReceipt(
                armed.arm, program, armed.initial, state, started, finished, elapsed, goal_met, status,
            )
            self._results[armed.arm] = result
            return result

    def abort(self) -> None:
        """Invalidate any pending ticket and prevent further execution or receipts."""
        self._aborted.set()
        with self._lock:
            self._armed = None

    def receipt(self) -> SimPairReceipt:
        """Summarize the local pair without accepting or importing arm results."""
        with self._lock:
            self._ensure_usable()
            if len(self._results) != 2:
                now = self._clock.now_ms
                if (
                    (self._pair_deadline_ms is not None and now >= self._pair_deadline_ms)
                    or (self._armed is not None and now >= self._armed.deadline_ms)
                ):
                    self._deadline_failure()
                raise SimulationBenchmarkError("Synthetic pair receipt is incomplete")
            baseline = self._results[SimArm.BASELINE]
            candidate = self._results[SimArm.CANDIDATE]
            comparable = baseline.goal_met and candidate.goal_met
            speedup = (
                round(100 * (baseline.elapsed_ms - candidate.elapsed_ms) / baseline.elapsed_ms, 3)
                if comparable else None
            )
            return SimPairReceipt(self._order[0], baseline, candidate, comparable, speedup)
