"""
Universal Game MCP (UGM) - Offline Strategy Simulator Adapter
Allows UGM to observe, read text, locate buttons, and interact with the built-in
UGM Offline Strategy Simulator GUI without needing commercial games installed.
"""

from __future__ import annotations
from typing import Dict, List, Optional, Any
from ..base import BaseGameAdapter
from ...core.types import NormalizedRect


class OfflineSimAdapter(BaseGameAdapter):
    """
    Adapter for the self-contained UGM Offline Strategy Simulator.
    """

    @property
    def game_id(self) -> str:
        return "offline_sim"

    @property
    def display_name(self) -> str:
        return "UGM Offline Strategy Simulator"

    @property
    def window_title_pattern(self) -> str:
        return r"UGM Offline Strategy Simulator"

    @property
    def process_names(self) -> List[str]:
        return ["python.exe", "pythonw.exe"]

    def get_ui_regions(self) -> Dict[str, NormalizedRect]:
        return {
            "topbar": NormalizedRect(0.0, 0.0, 1.0, 0.10),
            "gold": NormalizedRect(0.20, 0.0, 0.15, 0.09),
            "wood": NormalizedRect(0.35, 0.0, 0.15, 0.09),
            "mana": NormalizedRect(0.50, 0.0, 0.15, 0.09),
            "status": NormalizedRect(0.75, 0.0, 0.25, 0.09),
            "btn_farm": NormalizedRect(0.01, 0.88, 0.24, 0.10),
            "btn_soldier": NormalizedRect(0.26, 0.88, 0.24, 0.10),
            "btn_pause": NormalizedRect(0.51, 0.88, 0.24, 0.10),
            "btn_reset": NormalizedRect(0.76, 0.88, 0.22, 0.10),
            "playfield": NormalizedRect(0.05, 0.12, 0.90, 0.65),
        }

    def get_shortcuts(self) -> Dict[str, List[str]]:
        return {
            "pause": ["space"],
            "build": ["b"],
            "train": ["t"],
        }

    def register_icons(self, icon_detector: Any) -> None:
        pass
