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


def test_acceptance_provisional_evidence_rejects_coordinates_and_unverified_status():
    from scripts.smoke_windows_game_observation import validate_provisional_selector

    valid = {"provisional_id": "provisional_" + "a" * 32, "state_name": "unknown", "match": "found",
             "bbox_normalized": None, "action_suggested": False, "interactivity_verified": False,
             "evidence": {"status": "candidate", "observations": 1}}
    validate_provisional_selector(valid, valid["provisional_id"])
    for change in ({"bbox_normalized": {"x": 0.5}}, {"action_suggested": True}, {"state_name": "menu"},
                   {"match": "provisional_unconfirmed"}, {"interactivity_verified": True},
                   {"evidence": {"status": "unseen", "observations": 0}}):
        with pytest.raises(RuntimeError):
            validate_provisional_selector({**valid, **change}, valid["provisional_id"])


@pytest.mark.asyncio
async def test_repeat_smoke_never_passes_static_pixels():
    from scripts.smoke_windows_game_observation import check_provisional_learning

    calls = []
    async def call(tool, args):
        calls.append((tool, args))
        return {"status": "provisional_candidate", "state_name": "unknown", "receipt_count": 1,
                "action_suggested": False, "provisional_id": "provisional_" + "a" * 32}
    async def reattach():
        calls.append(("reattach", {}))

    with pytest.raises(RuntimeError, match="distinct"):
        await check_provisional_learning(call, reattach, "Settings", sample_interval=0)
    assert [tool for tool, _ in calls] == ["identify_provisional_ui"] * 3 + ["reattach"] + ["identify_provisional_ui"] * 3


@pytest.mark.asyncio
async def test_repeat_smoke_requires_fresh_identity_and_recorded_selector_receipt():
    from scripts.smoke_windows_game_observation import check_provisional_learning

    identity = "provisional_" + "a" * 32
    calls = []
    async def call(tool, args):
        calls.append((tool, args))
        if tool == "observe_ui_selector":
            return {"provisional_id": identity, "match": "found", "state_name": "unknown",
                    "bbox_normalized": None, "action_suggested": False, "interactivity_verified": False,
                    "evidence": {"status": "candidate", "observations": 1, "new_receipt": True}}
        reattached = any(name == "reattach" for name, _ in calls)
        matched = reattached
        return {"status": "provisional_match" if matched else "provisional_candidate",
                "state_name": "unknown", "receipt_count": 3 if matched else 2,
                "action_suggested": False, "provisional_id": identity}
    async def reattach():
        calls.append(("reattach", {}))

    assert await check_provisional_learning(call, reattach, "Settings", sample_interval=0) == 3
    assert calls[-1] == ("observe_ui_selector", {"selector_id": "acceptance", "label": "Settings",
                                                "provisional_id": identity, "record": True})
    assert calls[-2] == ("identify_provisional_ui", {"record": False})


@pytest.mark.asyncio
@pytest.mark.parametrize("target_valid", [True, False])
async def test_basic_smoke_reports_stages_and_never_calls_input(target_valid):
    import base64
    from types import SimpleNamespace
    from scripts.smoke_windows_game_observation import check_observations

    tools = []
    args = SimpleNamespace(title="Game", process="game.exe", expect_text="Play",
                           expect_anchor=None, learn_provisional=False)
    report = {"stages": {}}
    async def call(tool, params):
        tools.append(tool)
        if tool == "discover_and_attach_game":
            if params["window_title"] != "Game":
                return {"attached": False}
            return {"attached": True, "window": {
                "process_name": "game.exe", "hwnd": 11 if target_valid else 0, "pid": 100,
                "client_width": 80, "client_height": 60, "is_focused": True,
            }}
        if tool == "capture_screen":
            return {"width": 80, "height": 60, "image_base64": base64.b64encode(b"\x89PNG\r\n\x1a\n").decode()}
        if tool == "read_text":
            return {"full_text": "Play"}
        pytest.fail(f"Unexpected tool: {tool}")
    if target_valid:
        await check_observations(args, call, report)
        assert tools[-2:] == ["capture_screen", "read_text"]
        assert report["client_size"] == [80, 60]
        assert all(value == "pass" for value in report["stages"].values())
    else:
        with pytest.raises(RuntimeError, match="not attached"):
            await check_observations(args, call, report)
        assert tools == ["discover_and_attach_game"] * 2
        assert report["stages"]["focused_game_attachment"] == "blocked"
