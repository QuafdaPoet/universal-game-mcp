"""
Universal Game MCP (UGM) - Base Game Adapter Interface
Defines the plugin contract allowing game-specific adapters while keeping core independent.
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Any
import numpy as np

from ..core.types import NormalizedRect, ActionResult, WindowInfo


class BaseGameAdapter(ABC):
    """
    Abstract Base Class for Game Adapters.
    Encapsulates game-specific layout knowledge, UI regions, shortcuts, and icon dictionaries.
    """

    @property
    @abstractmethod
    def game_id(self) -> str:
        """Unique identifier of the game (e.g. 'hoi4')."""
        pass

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-readable game name."""
        pass

    @property
    @abstractmethod
    def window_title_pattern(self) -> str:
        """Regex pattern for window discovery."""
        pass

    @property
    @abstractmethod
    def process_names(self) -> List[str]:
        """Executable names associated with the game."""
        pass

    @abstractmethod
    def get_ui_regions(self) -> Dict[str, NormalizedRect]:
        """Returns standard named UI regions for the game."""
        pass

    @abstractmethod
    def get_shortcuts(self) -> Dict[str, List[str]]:
        """Returns mapping of common game actions to key sequences."""
        pass

    @abstractmethod
    def register_icons(self, icon_detector: Any) -> None:
        """Registers the game's icon templates into the icon detector."""
        pass
