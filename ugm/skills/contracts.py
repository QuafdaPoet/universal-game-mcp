"""
Universal Game MCP (UGM) - V1.5 Architectural Preparation Contracts
Defines lightweight abstract interfaces, enums, and dataclasses for future V1.5 subsystems:
- Game Skills Registry
- Persistent Knowledge Storage
- Menu Navigation Graph
- Teach / Explore / Reuse Modes
- Cross-Model Skill Sharing

Note: These are lightweight contractual specifications and interface definitions.
Advanced autonomous logic, online graph construction, and model training are reserved
for V1.5 and are NOT implemented in V1.
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from enum import Enum
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from .manifest import GameManifest, SelectorDefinition, SkillDefinition


class SkillExecutionMode(str, Enum):
    """Execution modes for skill invocation in V1.5."""
    REUSE = "reuse"      # Execute verified skill sequence deterministically (Default)
    EXPLORE = "explore"  # Probe UI cautiously to locate moved or altered targets
    TEACH = "teach"      # Interactive recording mode where human guides agent through steps


@dataclass
class NavigationNode:
    """Represents a discrete UI state / menu screen in a game's navigation graph."""
    node_id: str
    menu_name: str
    game_id: str
    identification_selector: str
    is_modal: bool = False
    parent_node_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class NavigationEdge:
    """Represents a transition between two menu nodes."""
    source_node_id: str
    target_node_id: str
    trigger_action: str  # e.g. "click_selector", "press_key"
    parameters: Dict[str, Any] = field(default_factory=dict)
    cost: float = 1.0


class MenuNavigationGraphContract(ABC):
    """
    Interface for Menu Navigation Graph (V1.5).
    Enables autonomous pathfinding across nested game menus and modal dialogues.
    """

    @abstractmethod
    def get_current_node(self, frame_state: Any) -> Optional[NavigationNode]:
        """Identifies active UI menu node from current visual frame."""
        pass

    @abstractmethod
    def find_path(self, start_node_id: str, goal_node_id: str) -> List[NavigationEdge]:
        """Calculates optimal sequence of actions to navigate from start to goal."""
        pass

    @abstractmethod
    def register_node(self, node: NavigationNode) -> None:
        """Registers a menu screen node."""
        pass

    @abstractmethod
    def register_edge(self, edge: NavigationEdge) -> None:
        """Registers a directed transition between two nodes."""
        pass


class PersistentKnowledgeStorageContract(ABC):
    """
    Interface for Persistent Game Knowledge Storage (V1.5).
    Stores learned anchor points, layout variations, and resolution-specific offsets.
    """

    @abstractmethod
    def save_knowledge(self, game_id: str, key: str, value: Dict[str, Any]) -> bool:
        """Persists knowledge item for a game."""
        pass

    @abstractmethod
    def get_knowledge(self, game_id: str, key: str) -> Optional[Dict[str, Any]]:
        """Retrieves knowledge item by key."""
        pass

    @abstractmethod
    def list_knowledge_keys(self, game_id: str) -> List[str]:
        """Lists all stored knowledge keys for a game."""
        pass


class GameSkillsRegistryContract(ABC):
    """
    Interface for Game Skills Registry (V1.5).
    Discovers, loads, version-checks, and hot-reloads Game Skill Packs.
    """

    @abstractmethod
    def register_pack(self, pack_path: str) -> GameManifest:
        """Discovers and registers a skill pack."""
        pass

    @abstractmethod
    def get_pack(self, game_id: str) -> Optional[Any]:
        """Returns loaded pack for game."""
        pass

    @abstractmethod
    def list_registered_games(self) -> List[str]:
        """Lists all registered game IDs."""
        pass


@dataclass
class SkillPackageBundle:
    """Standard format for cross-model skill distribution."""
    bundle_version: str
    game_id: str
    author: str
    skills: List[Dict[str, Any]]
    selectors: List[Dict[str, Any]]
    signature: Optional[str] = None


class CrossModelSkillShareContract(ABC):
    """
    Interface for Cross-Model Skill Sharing (V1.5).
    Enables exporting and importing skill definitions across Claude, GPT, and Gemini agents.
    """

    @abstractmethod
    def export_skill_bundle(self, game_id: str, skill_ids: List[str]) -> SkillPackageBundle:
        """Exports model-agnostic skill bundle."""
        pass

    @abstractmethod
    def import_skill_bundle(self, bundle: SkillPackageBundle) -> bool:
        """Imports and validates a model-agnostic skill bundle."""
        pass
