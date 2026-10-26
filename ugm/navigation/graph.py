"""
Universal Game MCP (UGM) - Persistent Navigation Graph
Implements MenuNavigationGraphContract with Dijkstra / BFS pathfinding,
dynamic cost calculation based on historical reliability, and SQLite persistence.
"""

from __future__ import annotations
import heapq
import json
import logging
from typing import Dict, List, Optional, Any, Set, Tuple
from dataclasses import dataclass, field

from ..skills.contracts import NavigationNode, NavigationEdge, MenuNavigationGraphContract
from ..memory.storage import PersistentKnowledgeStorage, TransitionRecord

logger = logging.getLogger("ugm.navigation.graph")


@dataclass
class PathResult:
    found: bool
    start_state: str
    target_state: str
    total_cost: float
    edges: List[NavigationEdge]
    nodes: List[str]
    message: str = ""


class NavigationGraph(MenuNavigationGraphContract):
    """
    Directed graph of recognized game UI states and verified transition actions.
    Uses Dijkstra's algorithm to plan optimal multi-step navigation routes.
    """

    def __init__(
        self,
        game_id: str = "hoi4",
        storage: Optional[PersistentKnowledgeStorage] = None
    ):
        self.game_id = game_id
        self.storage = storage
        self.nodes: Dict[str, NavigationNode] = {}
        self.adj_list: Dict[str, List[NavigationEdge]] = {}

        self._load_from_storage()

    def _load_from_storage(self) -> None:
        """Loads verified transitions from persistent database if available."""
        if not self.storage:
            return

        try:
            records = self.storage.get_transitions(self.game_id)
            for r in records:
                edge = NavigationEdge(
                    source_node_id=r.from_state,
                    target_node_id=r.to_state,
                    trigger_action=r.action_type,
                    parameters=r.params,
                    cost=r.cost
                )
                self.register_edge(edge, persist=False)
        except Exception as e:
            logger.debug("Navigation graph load notice: %s", e)

    def register_node(self, node: NavigationNode) -> None:
        """Registers a discrete UI state node in the graph."""
        self.nodes[node.node_id] = node
        if node.node_id not in self.adj_list:
            self.adj_list[node.node_id] = []

    def register_edge(self, edge: NavigationEdge, persist: bool = True) -> None:
        """Registers a directed transition between two state nodes."""
        src = edge.source_node_id
        tgt = edge.target_node_id

        if src not in self.adj_list:
            self.adj_list[src] = []
        if tgt not in self.adj_list:
            self.adj_list[tgt] = []

        # Avoid duplicate edges with exact same trigger and params
        existing = [e for e in self.adj_list[src] if e.target_node_id == tgt and e.trigger_action == edge.trigger_action and e.parameters == edge.parameters]
        if existing:
            # Update existing edge cost
            existing[0].cost = edge.cost
        else:
            self.adj_list[src].append(edge)

        if persist and self.storage:
            self.storage.record_transition(
                game_id=self.game_id,
                from_state=src,
                to_state=tgt,
                action_type=edge.trigger_action,
                params=edge.parameters,
                success=True,
                cost=edge.cost,
                source="live"
            )

    def get_current_node(self, frame_state: Any) -> Optional[NavigationNode]:
        """Looks up registered node matching state name string or detection object."""
        if isinstance(frame_state, str):
            return self.nodes.get(frame_state)
        elif hasattr(frame_state, "state_name"):
            return self.nodes.get(frame_state.state_name)
        return None

    def find_path(self, start_node_id: str, goal_node_id: str) -> List[NavigationEdge]:
        """Calculates optimal sequence of actions using Dijkstra algorithm."""
        res = self.plan_route(start_node_id, goal_node_id)
        return res.edges

    def plan_route(self, start_state: str, goal_state: str) -> PathResult:
        """
        Calculates optimal navigation route using Dijkstra's shortest path algorithm.
        """
        if start_state == goal_state:
            return PathResult(
                found=True,
                start_state=start_state,
                target_state=goal_state,
                total_cost=0.0,
                edges=[],
                nodes=[start_state],
                message="Already at target destination state."
            )

        # Dijkstra priority queue: (cumulative_cost, current_state, edge_path, node_path)
        pq: List[Tuple[float, str, List[NavigationEdge], List[str]]] = [(0.0, start_state, [], [start_state])]
        best_costs: Dict[str, float] = {start_state: 0.0}
        visited: Set[str] = set()

        while pq:
            cost, u, path_edges, path_nodes = heapq.heappop(pq)

            if u == goal_state:
                return PathResult(
                    found=True,
                    start_state=start_state,
                    target_state=goal_state,
                    total_cost=round(cost, 3),
                    edges=path_edges,
                    nodes=path_nodes,
                    message=f"Found optimal {len(path_edges)}-step route (cost: {cost:.2f})."
                )

            if u in visited:
                continue
            visited.add(u)

            for edge in self.adj_list.get(u, []):
                v = edge.target_node_id
                alt_cost = cost + max(0.1, edge.cost)
                if v not in best_costs or alt_cost < best_costs[v]:
                    best_costs[v] = alt_cost
                    heapq.heappush(pq, (alt_cost, v, path_edges + [edge], path_nodes + [v]))

        return PathResult(
            found=False,
            start_state=start_state,
            target_state=goal_state,
            total_cost=float("inf"),
            edges=[],
            nodes=[],
            message=f"No viable navigation path found from '{start_state}' to '{goal_state}'."
        )

    def record_transition_result(
        self,
        from_state: str,
        to_state: str,
        action_type: str,
        params: Dict[str, Any],
        success: bool,
        cost: float = 1.0,
        evidence: Optional[Dict[str, Any]] = None,
        source: str = "live"
    ) -> None:
        """Updates edge weights and records outcome in persistent storage."""
        if self.storage:
            self.storage.record_transition(
                game_id=self.game_id,
                from_state=from_state,
                to_state=to_state,
                action_type=action_type,
                params=params,
                success=success,
                cost=cost,
                evidence=evidence,
                source=source
            )

        # Adjust in-memory edge cost dynamically based on outcome
        for edge in self.adj_list.get(from_state, []):
            if edge.target_node_id == to_state and edge.trigger_action == action_type and edge.parameters == params:
                if success:
                    edge.cost = max(0.5, edge.cost * 0.95)
                else:
                    edge.cost = edge.cost + 2.0  # Penalize failing edges
                break
