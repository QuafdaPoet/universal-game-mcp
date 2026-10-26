"""
Universal Game MCP (UGM) - Generic Offline Game Adapter
Universal adapter capable of inspecting and interacting with any offline game or desktop app.
"""

from __future__ import annotations
from typing import Dict, List, Optional, Any
from ..base import BaseGameAdapter
from ...core.types import NormalizedRect


class GenericOfflineGameAdapter(BaseGameAdapter):
    """
    Fallback universal adapter for arbitrary offline games, emulators, or strategy apps.
    """

    def __init__(
        self,
        game_id: str = "generic_game",
        display_name: str = "Generic Offline Game",
        window_title: str = ".*",
        process_name: str = ".*"
    ):
        self._game_id = game_id
        self._display_name = display_name
        self._window_title = window_title
        self._process_name = process_name

    @property
    def game_id(self) -> str:
        return self._game_id

    @property
    def display_name(self) -> str:
        return self._display_name

    @property
    def window_title_pattern(self) -> str:
        return self._window_title

    @property
    def process_names(self) -> List[str]:
        return [self._process_name]

    def get_ui_regions(self) -> Dict[str, NormalizedRect]:
        return {
            "top_bar": NormalizedRect(0.0, 0.0, 1.0, 0.10),
            "bottom_bar": NormalizedRect(0.0, 0.90, 1.0, 0.10),
            "center": NormalizedRect(0.20, 0.20, 0.60, 0.60),
            "left_panel": NormalizedRect(0.0, 0.10, 0.25, 0.80),
            "right_panel": NormalizedRect(0.75, 0.10, 0.25, 0.80),
        }

    def get_shortcuts(self) -> Dict[str, List[str]]:
        return {
            "pause": ["space"],
            "confirm": ["enter"],
            "cancel": ["escape"],
            "inventory": ["i"],
            "map": ["m"],
        }

    def register_icons(self, icon_detector: Any) -> None:
        pass
