from types import SimpleNamespace

import pytest

from ugm.navigation.graph import NavigationEdge, NavigationGraph
from ugm.navigation.navigator import UINavigator


def make_navigation(monkeypatch, observations, edges):
    """Use real route planning and deterministic observations, never OS input."""
    observed = iter(observations)
    graph = NavigationGraph(game_id="fixture")
    for source, dest in edges:
        graph.register_edge(
            NavigationEdge(source, dest, "press_keys", {"keys": ["space"]}),
            persist=False,
        )

    def recognize_state(*args, **kwargs):
        state, status = next(observed)
        return SimpleNamespace(state_name=state, status=status)

    navigator = UINavigator(
        recognizer=SimpleNamespace(recognize_state=recognize_state, storage=None),
        graph=graph,
        capture_manager=SimpleNamespace(capture=lambda **kw: None),
        input_controller=SimpleNamespace(
            window_manager=SimpleNamespace(current_window=object()),
            is_dry_run_active=lambda dry_run: dry_run,
        ),
        target_executor=None,
        target_resolver=None,
    )
    dispatches = []

    def dispatch(edge, **kwargs):
        dispatches.append((edge.source_node_id, edge.target_node_id))
        return True, None

    monkeypatch.setattr(navigator, "_dispatch_navigation_action", dispatch)
    monkeypatch.setattr("ugm.navigation.navigator.time.sleep", lambda _: None)
    return navigator, dispatches


def test_replan_executes_only_the_replacement_route(monkeypatch):
    navigator, dispatches = make_navigation(
        monkeypatch,
        [("A", "known"), ("X", "known"), ("goal", "known")],
        [("A", "B"), ("B", "goal"), ("X", "goal")],
    )
    result = navigator.navigate_to("goal")
    assert result.success is True
    assert dispatches == [("A", "B"), ("X", "goal")]
    assert result.steps_executed == result.steps_total == 2
    assert [log["verified"] for log in result.step_logs] == [False, True]


@pytest.mark.parametrize("state,status", [("unknown", "unknown"), ("goal", "ambiguous")])
def test_unreliable_observation_stops_without_more_input(monkeypatch, state, status):
    navigator, dispatches = make_navigation(
        monkeypatch,
        [("A", "known"), (state, status), ("goal", "known")],
        [("A", "B"), ("B", "goal"), ("unknown", "goal")],
    )
    result = navigator.navigate_to("goal")
    assert result.success is False
    assert dispatches == [("A", "B")]
    assert result.steps_executed == 1
    assert status in result.message


def test_ambiguous_start_is_not_a_navigation_source(monkeypatch):
    navigator, dispatches = make_navigation(
        monkeypatch,
        [("A", "ambiguous"), ("goal", "known")],
        [("A", "goal")],
    )
    result = navigator.navigate_to("goal")
    assert result.success is False
    assert dispatches == []


def test_replan_respects_retry_limit(monkeypatch):
    navigator, dispatches = make_navigation(
        monkeypatch,
        [("A", "known"), ("X", "known"), ("Y", "known")],
        [("A", "goal"), ("X", "goal"), ("Y", "goal")],
    )
    result = navigator.navigate_to("goal", max_retries=1)
    assert result.success is False
    assert dispatches == [("A", "goal"), ("X", "goal")]
    assert result.steps_executed == 2


def test_replan_respects_cumulative_step_limit(monkeypatch):
    navigator, dispatches = make_navigation(
        monkeypatch,
        [("A", "known"), ("X", "known")],
        [("A", "B"), ("B", "goal"), ("X", "Y"), ("Y", "goal")],
    )
    monkeypatch.setattr(navigator, "MAX_NAVIGATION_STEPS", 2)
    result = navigator.navigate_to("goal")
    assert result.success is False
    assert dispatches == [("A", "B")]
    assert "2-step limit" in result.message
