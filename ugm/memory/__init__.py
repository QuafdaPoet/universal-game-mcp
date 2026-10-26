"""
Universal Game MCP (UGM) - Persistent Memory Subsystem
"""

from .database import DatabaseManager, compute_dhash, hamming_distance
from .storage import (
    PersistentKnowledgeStorage,
    UIStateRecord,
    TransitionRecord,
    LearnedSkillRecord
)

__all__ = [
    "DatabaseManager",
    "compute_dhash",
    "hamming_distance",
    "PersistentKnowledgeStorage",
    "UIStateRecord",
    "TransitionRecord",
    "LearnedSkillRecord",
]
