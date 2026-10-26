"""
Universal Game MCP (UGM) - Persistent Memory Subsystem
"""

try:
    from .database import DatabaseManager, compute_dhash, hamming_distance
    from .storage import (
        PersistentKnowledgeStorage,
        UIStateRecord,
        TransitionRecord,
        LearnedSkillRecord
    )
except ImportError:
    DatabaseManager = None  # type: ignore
    compute_dhash = None  # type: ignore
    hamming_distance = None  # type: ignore
    PersistentKnowledgeStorage = None  # type: ignore
    UIStateRecord = None  # type: ignore
    TransitionRecord = None  # type: ignore
    LearnedSkillRecord = None  # type: ignore

__all__ = [
    "DatabaseManager",
    "compute_dhash",
    "hamming_distance",
    "PersistentKnowledgeStorage",
    "UIStateRecord",
    "TransitionRecord",
    "LearnedSkillRecord",
]
