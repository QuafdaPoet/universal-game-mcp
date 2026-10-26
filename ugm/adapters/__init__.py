"""
Universal Game MCP (UGM) - Game Adapters
"""

from .base import BaseGameAdapter
from .hoi4.adapter import HOI4Adapter
from .sim.adapter import OfflineSimAdapter
from .generic.adapter import GenericOfflineGameAdapter

__all__ = [
    "BaseGameAdapter",
    "HOI4Adapter",
    "OfflineSimAdapter",
    "GenericOfflineGameAdapter",
]
