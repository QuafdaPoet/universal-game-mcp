"""
Universal Game MCP (UGM) - Hearts of Iron IV (HOI4) Plugin Adapter
Provides UI layout maps, hotkeys, icon templates, and topbar state parser for HOI4.
"""

from __future__ import annotations
import os
import re
import logging
from typing import Dict, List, Optional, Any
import numpy as np
import cv2

from ..base import BaseGameAdapter
from ...core.types import NormalizedRect, OCRResult
from ...core.ocr import BaseOCREngine
from .knowledge import HOI4KnowledgeManager
from .perception import (
    parse_hoi4_date,
    detect_pause_state,
    detect_speed_level,
    detect_tooltip_box,
    detect_popups
)
from .environment import HOI4EnvironmentDetector

logger = logging.getLogger("ugm.adapter.hoi4")


class HOI4Adapter(BaseGameAdapter):
    """
    Hearts of Iron IV adapter plugin.
    Maps topbar statistics, speed controls, production queues, and DirectInput hotkeys.
    Provides prebuilt campaign knowledge, advanced perception, and environment discovery.
    """

    def __init__(self, knowledge_dir: Optional[str] = None):
        self.knowledge_manager = HOI4KnowledgeManager(knowledge_dir)
        self.env_detector = HOI4EnvironmentDetector()

    @property
    def game_id(self) -> str:
        return "hoi4"

    @property
    def display_name(self) -> str:
        return "Hearts of Iron IV"

    @property
    def window_title_pattern(self) -> str:
        return r"Hearts of Iron IV"

    @property
    def process_names(self) -> List[str]:
        return ["hoi4.exe"]

    def get_ui_regions(self) -> Dict[str, NormalizedRect]:
        """
        Normalized coordinates for key HOI4 UI elements (based on standard 1080p layout).
        """
        return {
            # Top resource & national statistics bar
            "topbar": NormalizedRect(x=0.0, y=0.0, width=1.0, height=0.05),
            "political_power": NormalizedRect(x=0.04, y=0.0, width=0.055, height=0.04),
            "stability": NormalizedRect(x=0.095, y=0.0, width=0.055, height=0.04),
            "war_support": NormalizedRect(x=0.150, y=0.0, width=0.055, height=0.04),
            "manpower": NormalizedRect(x=0.205, y=0.0, width=0.070, height=0.04),
            "factories": NormalizedRect(x=0.275, y=0.0, width=0.065, height=0.04),
            "convoys": NormalizedRect(x=0.340, y=0.0, width=0.060, height=0.04),
            "command_power": NormalizedRect(x=0.400, y=0.0, width=0.060, height=0.04),

            # Time and speed controls (top-right)
            "date_clock": NormalizedRect(x=0.83, y=0.0, width=0.10, height=0.045),
            "speed_controls": NormalizedRect(x=0.93, y=0.0, width=0.07, height=0.045),
            "pause_indicator": NormalizedRect(x=0.85, y=0.045, width=0.08, height=0.035),

            # Navigation buttons (top-left tab cluster)
            "decisions_tab": NormalizedRect(x=0.015, y=0.05, width=0.03, height=0.04),
            "research_tab": NormalizedRect(x=0.048, y=0.05, width=0.03, height=0.04),
            "diplomacy_tab": NormalizedRect(x=0.081, y=0.05, width=0.03, height=0.04),
            "trade_tab": NormalizedRect(x=0.114, y=0.05, width=0.03, height=0.04),
            "construction_tab": NormalizedRect(x=0.147, y=0.05, width=0.03, height=0.04),
            "production_tab": NormalizedRect(x=0.180, y=0.05, width=0.03, height=0.04),
            "recruit_tab": NormalizedRect(x=0.213, y=0.05, width=0.03, height=0.04),
            "logistics_tab": NormalizedRect(x=0.246, y=0.05, width=0.03, height=0.04),

            # Main content areas
            "center_screen": NormalizedRect(x=0.25, y=0.20, width=0.50, height=0.60),
            "event_dialog": NormalizedRect(x=0.28, y=0.25, width=0.44, height=0.50),
            "theatre_panel": NormalizedRect(x=0.82, y=0.12, width=0.18, height=0.75),
        }

    def get_shortcuts(self) -> Dict[str, List[str]]:
        """
        DirectInput key bindings for HOI4.
        """
        return {
            "pause": ["space"],
            "speed_up": ["num+"],
            "speed_down": ["num-"],
            "decisions": ["shift", "q"],
            "research": ["w"],
            "diplomacy": ["e"],
            "trade": ["r"],
            "construction": ["t"],
            "production": ["y"],
            "recruit": ["u"],
            "logistics": ["i"],
            "air_map": ["f3"],
            "navy_map": ["f2"],
            "army_map": ["f1"],
            "escape": ["escape"],
        }

    def register_icons(self, icon_detector: Any) -> None:
        """
        Loads icons from local icon directory or registers default icon templates.
        """
        icons_dir = os.path.join(os.path.dirname(__file__), "icons")
        if os.path.exists(icons_dir):
            for file_name in os.listdir(icons_dir):
                if file_name.endswith((".png", ".jpg")):
                    icon_name = os.path.splitext(file_name)[0]
                    file_path = os.path.join(icons_dir, file_name)
                    try:
                        icon_detector.load_template_file(icon_name, file_path)
                    except Exception as e:
                        logger.warning("Could not load icon %s: %s", file_name, e)

    def parse_topbar(
        self,
        ocr_engine: BaseOCREngine,
        frame: np.ndarray,
        detailed: bool = True
    ) -> Dict[str, Any]:
        """
        Performs event-driven OCR on the topbar regions to extract key national metrics.
        Optionally extracts date, pause status, and simulation speed.
        """
        regions = self.get_ui_regions()
        metrics: Dict[str, Any] = {}

        for stat_name in ["political_power", "stability", "war_support", "manpower", "factories"]:
            roi = regions[stat_name]
            res = ocr_engine.read_text(frame, roi=roi, event_tag=f"hoi4_topbar_{stat_name}")
            metrics[stat_name] = {
                "text": res.full_text,
                "confidence": max([i.confidence for i in res.items], default=0.0),
                "items": [i.to_dict() for i in res.items]
            }

        if detailed:
            # Date & Clock parsing
            date_roi = regions.get("date_clock")
            if date_roi:
                date_res = ocr_engine.read_text(frame, roi=date_roi, event_tag="hoi4_topbar_date")
                parsed_date = parse_hoi4_date(date_res.full_text)
                metrics["date"] = {
                    "raw_text": date_res.full_text,
                    "parsed": parsed_date,
                    "confidence": max([i.confidence for i in date_res.items], default=0.0)
                }

            # Pause state detection
            pause_state, pause_details = detect_pause_state(frame)
            metrics["pause"] = {
                "state": pause_state,
                "details": pause_details
            }

            # Speed level detection
            metrics["speed"] = {
                "level": detect_speed_level(frame)
            }

        return metrics

    def get_knowledge(self, topic: str, subtopic: Optional[str] = None) -> Dict[str, Any]:
        """Loads prebuilt campaign or country strategy on-demand."""
        return self.knowledge_manager.get_knowledge(topic, subtopic)

    def detect_environment(self, custom_game_path: Optional[str] = None) -> Dict[str, Any]:
        """Discovers local HOI4 installation, process status, and OCR readiness."""
        return self.env_detector.detect(custom_game_path)

    def parse_date(self, text: str) -> Optional[Dict[str, Any]]:
        """Parses in-game date strings (e.g. '12:00, 1 Jan, 1936')."""
        return parse_hoi4_date(text)

    def detect_pause(self, frame: np.ndarray, scale: float = 1.0) -> Tuple[str, Dict[str, Any]]:
        """Detects whether game is paused via red hazard stripe detection."""
        return detect_pause_state(frame, scale=scale)

    def detect_speed(self, frame: np.ndarray, scale: float = 1.0) -> Optional[int]:
        """Detects current simulation speed (1-5)."""
        return detect_speed_level(frame, scale=scale)

    def detect_tooltip(
        self,
        before_frame: np.ndarray,
        after_frame: np.ndarray,
        cursor_xy: Tuple[int, int] = (960, 540),
        max_dist: int = 700,
        min_area: int = 1200
    ) -> Optional[List[int]]:
        """Detects tooltip bounding box from before-and-after visual differential."""
        return detect_tooltip_box(before_frame, after_frame, cursor_xy=cursor_xy, max_dist=max_dist, min_area=min_area)

    def detect_popups(self, frame: np.ndarray) -> List[Dict[str, Any]]:
        """Detects paper mask modal dialogs or news popups."""
        return detect_popups(frame)
