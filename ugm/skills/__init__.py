"""
Universal Game MCP (UGM) - Game Skills Subsystem
"""

from .manifest import (
    GameManifest,
    GameMetadata,
    SelectorDefinition,
    SkillDefinition,
    ManifestValidationError
)
from .loader import GameSkillPack, GameSkillPackLoader
from .contracts import (
    SkillExecutionMode,
    NavigationNode,
    NavigationEdge,
    MenuNavigationGraphContract,
    PersistentKnowledgeStorageContract,
    GameSkillsRegistryContract,
    CrossModelSkillShareContract,
    SkillPackageBundle
)

__all__ = [
    "GameManifest",
    "GameMetadata",
    "SelectorDefinition",
    "SkillDefinition",
    "ManifestValidationError",
    "GameSkillPack",
    "GameSkillPackLoader",
    "SkillExecutionMode",
    "NavigationNode",
    "NavigationEdge",
    "MenuNavigationGraphContract",
    "PersistentKnowledgeStorageContract",
    "GameSkillsRegistryContract",
    "CrossModelSkillShareContract",
    "SkillPackageBundle",
]
