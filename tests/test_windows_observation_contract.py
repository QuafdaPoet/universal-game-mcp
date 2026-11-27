import pytest

from scripts.smoke_windows_game_observation import validate_native_triage


def eligible(**kwargs):
    return {
        "source": "dry_run_observation", "samples": 2, "status": "stable_candidate",
        "state_name": "unknown", "action_suggested": False, "anchors": ["play", "settings"],
        **kwargs,
    }


def test_read_only_native_triage_accepts_independently_repeated_labels():
    assert validate_native_triage(eligible(), " Settings ") == 2


@pytest.mark.parametrize("change", [
    {"source": "offline_fixture"}, {"samples": 1}, {"action_suggested": True},
    {"state_name": "main"}, {"anchors": ["settings", "settings"]}, {"anchors": ["play", "resume"]},
    {"status": "insufficient_evidence"}, {"route": ["menu"]},
])
def test_read_only_native_triage_refuses_nonconforming_evidence(change):
    with pytest.raises(RuntimeError):
        validate_native_triage(eligible(**change), "settings")
