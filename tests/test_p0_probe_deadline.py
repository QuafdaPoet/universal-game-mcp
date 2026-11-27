"""Cooperative probe deadline, stable visual evidence, and safe dispatch regressions."""

from types import SimpleNamespace

import numpy as np
import pytest

from ugm.core import explorer as explorer_module
from ugm.core.explorer import UIExplorer
from ugm.core.input import InputController
from ugm.core.safety import SafetyViolationError
from ugm.core.verifier import ActionVerifier

BASE = np.zeros((16, 16, 3), dtype=np.uint8)
CHANGE = np.full_like(BASE, 200)


class FakeClock:
    current = 10.0

    def now(self):
        return self.current

    def sleep(self, seconds):
        assert seconds >= 0
        self.current += seconds


@pytest.fixture
def probe():
    clock = FakeClock()
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(explorer_module, "time", SimpleNamespace(
            perf_counter=clock.now, sleep=clock.sleep,
        ))
        calls = []
        window = SimpleNamespace(process_name="test.exe", title="Offline game")
        controller = InputController(SimpleNamespace(current_window=window))
        patch.setattr(controller, "press_keys", lambda **kw: (
            calls.append(("press_keys", kw)) or SimpleNamespace(success=True, message="sent")
        ))
        patch.setattr(controller, "click", lambda **kw: (
            calls.append(("click", kw)) or
            SimpleNamespace(executed=True, dry_run=False, status="success")
        ))

        def setup(frames, ocr=None):
            def require_bound(manager, expected):
                if expected is None or manager.current_window is not expected:
                    raise SafetyViolationError("The attached window changed before probe dispatch.")
                return expected

            capture = SimpleNamespace(
                capture=frames,
                capture_attached_window=lambda manager, expected: frames(window=expected),
                require_attached_window=require_bound,
            )
            engine = SimpleNamespace(read_text=ocr or (lambda *a, **k: SimpleNamespace(items=[])))
            verifier = ActionVerifier(capture, engine, controller)
            explorer = UIExplorer(capture, controller, verifier, engine, None)
            return explorer

        yield clock, calls, setup


def test_baseline_that_consumes_budget_never_dispatches(probe):
    clock, calls, setup = probe

    def capture(**kw):
        clock.sleep(0.2)
        return BASE

    result = setup(capture).probe_action(
        "press_keys", {"keys": ["space"]}, timeout_s=0.1,
    )
    assert calls == []
    assert result["timed_out"] and result["timeout_phase"] == "baseline_capture"
    assert result["outcome"] == "timeout"
    assert not result["executed"] and not result["verification_passed"]


def test_delayed_stable_update_uses_baseline_ocr_set_difference(probe):
    clock, calls, setup = probe
    start = clock.now()

    def capture(**kw):
        return CHANGE if clock.now() - start >= 0.2 else BASE

    def ocr(frame, **kw):
        values = [" Credits    Remaining ", "Old panel"] if frame[0, 0, 0] == 0 else [
            "credits remaining", "New panel", "new  panel", "A",
        ]
        return SimpleNamespace(items=[SimpleNamespace(text=text) for text in values])

    result = setup(capture, ocr).probe_action(
        "click", {"x": 0.2, "y": 0.6}, timeout_s=0.8,
    )
    assert [action for action, _ in calls] == ["click"]
    assert clock.now() - start >= 0.3
    assert result["outcome"] == "major_state_transition"
    assert result["visual_change_detected"]
    assert result["evidence_level"] == "observed"
    assert result["new_text_detected"] == ["New panel"]
    assert not result["timed_out"]


def test_transient_flash_is_not_observed_as_state_change(probe):
    clock, calls, setup = probe
    start = clock.now()

    def capture(**kw):
        elapsed = clock.now() - start
        return CHANGE if 0.08 < elapsed < 0.16 else BASE

    result = setup(capture).probe_action("wait", {"duration_s": 0.05}, timeout_s=0.45)
    assert not calls
    assert result["outcome"] == "no_visual_change"
    assert result["transient_change_detected"]
    assert not result["verification_passed"]
    assert not result["timed_out"]


def test_unstable_roi_has_timeout_not_verified(probe):
    clock, _, setup = probe
    count = 0

    def capture(**kw):
        nonlocal count
        count += 1
        return CHANGE if count % 2 == 0 else BASE

    result = setup(capture).probe_action("wait", {"duration_s": 0.05}, timeout_s=0.45)
    assert result["outcome"] == "timeout"
    assert result["timeout_phase"] == "observation"
    assert not result["verification_passed"]


def test_missing_or_nonfinite_coordinates_reject_before_capture(probe):
    _, calls, setup = probe

    def capture(**kw):
        pytest.fail("invalid action reached capture")

    explorer = setup(capture)
    for action, params in [("click", {}), ("hover", {"x": 0.0}),
                           ("click", {"x": float("nan"), "y": 0.5})]:
        with pytest.raises(SafetyViolationError):
            explorer.probe_action(action, params)
    assert calls == []


def test_ocr_overrun_discards_text_but_keeps_already_stable_visual_evidence(probe):
    clock, _, setup = probe
    start = clock.now()
    ocr_calls = []

    def capture(**kw):
        return CHANGE if clock.now() - start > 0.08 else BASE

    def ocr(frame, **kw):
        ocr_calls.append(kw["event_tag"])
        clock.sleep(0.8)
        return SimpleNamespace(items=[SimpleNamespace(text="unusable text")])

    result = setup(capture, ocr).probe_action("wait", {"duration_s": 0.05}, timeout_s=0.6)
    assert ocr_calls == ["probe_baseline_check"]
    assert result["outcome"] == "major_state_transition"
    assert result["visual_change_detected"]
    assert result["timed_out"] and result["timeout_phase"] == "ocr"
    assert result["new_text_detected"] == []


def test_deadline_cannot_fit_declared_input_never_dispatches(probe):
    _, calls, setup = probe
    explorer = setup(lambda **kw: BASE)
    result = explorer.probe_action("press_keys", {"keys": ["space"], "duration_ms": 500}, timeout_s=0.4)
    assert calls == []
    assert result["outcome"] == "timeout"
    assert result["timeout_phase"] == "dispatch"
    assert result["dispatch_status"] == "not_attempted"


def test_dispatch_overrun_is_not_retried_or_followed_by_observation(probe, monkeypatch):
    clock, calls, setup = probe

    def send(**kw):
        calls.append("input")
        clock.sleep(2)
        return SimpleNamespace(success=True, message="sent", dry_run=False)

    explorer = setup(lambda **kw: calls.append("capture") or BASE)
    monkeypatch.setattr(explorer.input_controller, "press_keys", send)
    result = explorer.probe_action("press_keys", {"keys": ["space"]}, timeout_s=0.5, revert_on_fail=True)
    assert calls == ["capture", "input"]
    assert result["executed"]
    assert result["outcome"] == "timeout"
    assert result["timeout_phase"] == "dispatch"
    assert not result["reverted"]
    assert not result["visual_diff_measured"]


def test_target_switch_after_baseline_blocks_dispatch(probe):
    _, calls, setup = probe

    def capture(**kw):
        explorer.input_controller.window_manager.current_window = SimpleNamespace(process_name="test.exe", title="Offline game")
        return BASE

    explorer = setup(capture)
    result = explorer.probe_action("press_keys", {"keys": ["space"]})
    assert calls == []
    assert result["outcome"] == "blocked"
    assert result["dispatch_status"] == "not_attempted"
    assert result["observation_error"]


def test_geometry_drift_does_not_generate_delta(probe):
    _, calls, setup = probe
    frames = iter([BASE, np.zeros((18, 16, 3), dtype=np.uint8)])
    result = setup(lambda **kw: next(frames)).probe_action("press_keys", {"keys": ["space"]}, timeout_s=0.5)
    assert calls and result["executed"]
    assert result["outcome"] == "observation_error"
    assert result["diff_ratio"] == 0.0
    assert not result["visual_diff_measured"]


def test_invalid_roi_and_timeout_do_not_capture(probe):
    from ugm.core.types import NormalizedRect

    _, calls, setup = probe

    def capture(**kw):
        pytest.fail("invalid probe parameters reached live capture")

    explorer = setup(capture)
    for timeout in [float("nan"), 0.01, 11.0]:
        with pytest.raises(SafetyViolationError):
            explorer.probe_action("wait", timeout_s=timeout)
    for roi in [NormalizedRect(0.5, 0.5, 0, 0.1), NormalizedRect(0.9, 0.5, 0.2, 0.1)]:
        with pytest.raises(SafetyViolationError):
            explorer.probe_action("wait", expected_roi=roi)
    assert calls == []
