"""Rejected navigation routes cannot silently become executable again."""
from types import SimpleNamespace

import pytest

from ugm.memory.database import DatabaseManager
from ugm.memory.storage import PersistentKnowledgeStorage
from ugm.navigation.graph import NavigationEdge, NavigationGraph
from ugm.navigation.navigator import UINavigator
from ugm.server import UGMContext, create_server


EDGE = NavigationEdge("main_map", "production", "press_keys", {"keys": ["y"]}, cost=1.0)


def storage_at(path):
    return PersistentKnowledgeStorage(DatabaseManager(path))


def record(storage, game, edge=EDGE, *, success=True, status=None):
    storage.record_transition(
        game, edge.source_node_id, edge.target_node_id,
        edge.trigger_action, edge.parameters, success=success, status=status,
    )


def test_rejected_record_immediately_removes_cached_and_reseeded_route(tmp_path):
    store = storage_at(tmp_path / "memory.db")
    record(store, "hoi4")
    graph = NavigationGraph(game_id="hoi4", storage=store)
    graph.register_edge(EDGE, persist=False)
    assert graph.plan_route("main_map", "production").found

    row = store.get_transitions("hoi4")[0]
    assert store.update_knowledge_state("hoi4", "transition", str(row.id), "rejected")
    assert not graph.plan_route("main_map", "production").found
    reopened = NavigationGraph(game_id="hoi4", storage=storage_at(tmp_path / "memory.db"))
    reopened.register_edge(EDGE, persist=False)
    assert not reopened.plan_route("main_map", "production").found


@pytest.mark.parametrize("status", ["candidate", "suspect"])
def test_unverified_learned_routes_never_used_for_planning(tmp_path, status):
    store = storage_at(tmp_path / "memory.db")
    record(store, "game", status=status)
    assert not NavigationGraph("game", store).plan_route("main_map", "production").found


def test_checkpoint_before_rejection_does_not_unquarantine_route(tmp_path):
    store = storage_at(tmp_path / "memory.db")
    record(store, "hoi4")
    checkpoint_id = store.create_checkpoint("hoi4", "before rejection")
    row = store.get_transitions("hoi4")[0]
    assert store.update_knowledge_state("hoi4", "transition", str(row.id), "rejected")
    assert store.rollback_checkpoint("hoi4", checkpoint_id)
    record(store, "hoi4", status="verified")
    graph = NavigationGraph("hoi4", store)
    graph.register_edge(EDGE, persist=False)
    assert not graph.plan_route("main_map", "production").found


@pytest.mark.asyncio
async def test_can_quarantine_a_curated_edge_before_it_has_a_row(tmp_path):
    ctx = UGMContext(offline_fixture="tests/fixtures/hoi4_topbar.png",
                     ocr_backend="mock", db_path=tmp_path / "memory.db")
    graph = ctx.get_navigation_graph("hoi4")
    assert graph.plan_route("main_map", "production").found
    assert not ctx.storage.get_transitions("hoi4")
    result = await create_server(ctx).call_tool("manage_knowledge_state", {
        "item_type": "transition", "item_id": "", "status": "rejected",
        "game_id": "hoi4", "transition_identity": {
            "from_state": "main_map", "to_state": "production",
            "action_type": "press_keys", "params": {"keys": ["y"]},
        },
    })
    body = result.structured_content.get("result", result.structured_content)
    assert body["success"]
    assert not graph.plan_route("main_map", "production").found
    assert graph.plan_route("main_map", "research").found


def test_quarantine_is_game_scoped_and_canonicalizes_params(tmp_path):
    store = storage_at(tmp_path / "memory.db")
    stored = NavigationEdge("a", "b", "click_target", {"x": 0.2, "y": 0.4}, cost=1.0)
    reordered = NavigationEdge("a", "b", "click_target", {"y": 0.4, "x": 0.2}, cost=1.0)
    alternate = NavigationEdge("a", "b", "press_keys", {"keys": ["t"]}, cost=2.0)
    record(store, "a_game", stored, success=False, status="rejected")
    first, second = NavigationGraph("a_game", store), NavigationGraph("other_game", store)
    first.register_edge(reordered, persist=False)
    first.register_edge(alternate, persist=False)
    second.register_edge(reordered, persist=False)
    assert first.plan_route("a", "b").edges[0].trigger_action == "press_keys"
    assert second.plan_route("a", "b").edges[0].trigger_action == "click_target"


def test_rejected_queued_step_never_dispatches(tmp_path, monkeypatch):
    store = storage_at(tmp_path / "memory.db")
    record(store, "hoi4")
    graph = NavigationGraph("hoi4", store)
    plan = graph.plan_route

    def concurrently_rejected(*a, **kw):
        result = plan(*a, **kw)
        row = store.get_transitions("hoi4")[0]
        store.update_knowledge_state("hoi4", "transition", str(row.id), "rejected")
        return result

    monkeypatch.setattr(graph, "plan_route", concurrently_rejected)
    navigator = UINavigator(
        recognizer=SimpleNamespace(storage=None,
            recognize_state=lambda *a, **kw: SimpleNamespace(status="known", state_name="main_map")),
        graph=graph, capture_manager=SimpleNamespace(capture=lambda **kw: None),
        input_controller=SimpleNamespace(is_dry_run_active=lambda _: False,
            window_manager=SimpleNamespace(current_window=object())),
        target_executor=None, target_resolver=None,
    )
    monkeypatch.setattr(navigator, "_dispatch_navigation_action",
                        lambda *a, **kw: pytest.fail("A rejected queued edge sent input"))
    result = navigator.navigate_to("production")
    assert not result.success and result.steps_executed == 0


def test_v3_upgrade_retains_an_existing_rejected_edge(tmp_path):
    import sqlite3

    path = tmp_path / "prior.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at REAL NOT NULL)")
        DatabaseManager.__new__(DatabaseManager)._migrate(conn, from_ver=0, to_ver=3)
        conn.execute("""
            INSERT INTO navigation_transitions (
                game_id, from_state, to_state, action_type, params, cost, success_count,
                fail_count, confidence, source, created_at, updated_at, status, consecutive_failures
            ) VALUES ('hoi4', 'main_map', 'production', 'press_keys', '{"keys":["y"]}', 1, 0,
                      3, 0, 'live', 1000, 2000, 'rejected', 3)
        """)

    store = storage_at(path)
    graph = NavigationGraph("hoi4", store)
    graph.register_edge(EDGE, persist=False)
    assert store.get_quarantined_routes("hoi4") == {
        ("main_map", "production", "press_keys", '{"keys": ["y"]}'),
    }
    assert not graph.plan_route("main_map", "production").found


def test_automatic_rejection_is_durable_after_verified_status_assignment(tmp_path):
    store = storage_at(tmp_path / "memory.db")
    for _ in range(3):
        record(store, "game", success=False)
    rejected = store.get_transitions("game", exclude_rejected=False)[0]
    assert rejected.status == "rejected"
    assert store.update_knowledge_state("game", "transition", str(rejected.id), "verified")
    assert not NavigationGraph("game", store).plan_route("main_map", "production").found
