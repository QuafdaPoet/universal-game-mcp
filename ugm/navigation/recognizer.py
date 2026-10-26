"""
Universal Game MCP (UGM) - UI State Recognition Engine
Classifies the current game interface using multiple observable signals:
visual anchors, event-driven OCR, panel geometry, and perceptual hashes.
"""

from __future__ import annotations
import time
import re
import logging
from typing import Optional, List, Dict, Any, Tuple
from dataclasses import dataclass, asdict
import numpy as np
import cv2

from ..core.types import NormalizedRect, OCRResult
from ..core.ocr import BaseOCREngine
from ..core.icon import IconDetector
from ..memory.database import compute_dhash, hamming_distance
from ..memory.storage import PersistentKnowledgeStorage

from ..adapters.hoi4.perception import detect_popups

logger = logging.getLogger("ugm.navigation.recognizer")


@dataclass
class UIStateDetection:
    game_id: str
    state_name: str
    confidence: float
    status: str  # 'known', 'ambiguous', 'unknown'
    signals: Dict[str, Any]
    evidence: Dict[str, Any]
    phash: str
    timestamp: float

    @property
    def is_known(self) -> bool:
        return self.status == "known"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class UIStateRecognizer:
    """
    Multi-signal game interface classifier.
    Combines visual template matching, OCR text patterns, panel geometry,
    and perceptual hash memory matching.
    """

    def __init__(
        self,
        ocr_engine: BaseOCREngine,
        icon_detector: IconDetector,
        storage: Optional[PersistentKnowledgeStorage] = None,
        confidence_threshold: float = 0.65
    ):
        self.ocr_engine = ocr_engine
        self.icon_detector = icon_detector
        self.storage = storage
        self.confidence_threshold = confidence_threshold

    def recognize_state(
        self,
        frame: np.ndarray,
        game_id: str = "hoi4",
        hint_state: Optional[str] = None,
        source: Optional[str] = None
    ) -> UIStateDetection:
        """
        Classifies the active UI state from a video frame.
        """
        t0 = time.time()
        phash = compute_dhash(frame)
        signals: Dict[str, Any] = {}
        candidate_scores: Dict[str, float] = {}
        evidence: Dict[str, Any] = {"phash": phash}

        h, w = frame.shape[:2]

        # ----------------------------------------------------
        # Signal 1: Perceptual Hash match against learned memory
        # ----------------------------------------------------
        if self.storage is not None:
            mem_match = self.storage.find_matching_state(game_id, phash, max_distance=6)
            if mem_match:
                mem_state, mem_conf, dist = mem_match
                mem_score = max(0.5, 1.0 - (dist / 10.0))
                candidate_scores[mem_state] = candidate_scores.get(mem_state, 0.0) + (mem_score * 0.4)
                signals["phash_match"] = {
                    "state": mem_state,
                    "distance": dist,
                    "score": round(mem_score, 3)
                }

        # ----------------------------------------------------
        # Signal 2: Game-Specific Logic & Anchors
        # ----------------------------------------------------
        if game_id == "hoi4":
            self._evaluate_hoi4_signals(frame, candidate_scores, signals, evidence)
        elif game_id == "offline_sim":
            self._evaluate_sim_signals(frame, candidate_scores, signals, evidence)
        else:
            self._evaluate_generic_signals(frame, candidate_scores, signals, evidence)

        # ----------------------------------------------------
        # Consolidate and Classify
        # ----------------------------------------------------
        if not candidate_scores:
            return UIStateDetection(
                game_id=game_id,
                state_name="unknown",
                confidence=0.0,
                status="unknown",
                signals=signals,
                evidence=evidence,
                phash=phash,
                timestamp=t0
            )

        # Sort candidates by combined score
        ranked = sorted(candidate_scores.items(), key=lambda x: x[1], reverse=True)
        top_state, top_score = ranked[0]
        norm_confidence = min(1.0, round(top_score, 3))

        # Check for ambiguity
        is_ambiguous = False
        if len(ranked) > 1:
            second_state, second_score = ranked[1]
            if second_score >= top_score * 0.90 and (top_score - second_score) < 0.15:
                is_ambiguous = True
                signals["competing_candidate"] = {"state": second_state, "score": round(second_score, 3)}

        if is_ambiguous:
            status = "ambiguous"
        elif norm_confidence >= self.confidence_threshold:
            status = "known"
        else:
            status = "unknown"

        result = UIStateDetection(
            game_id=game_id,
            state_name=top_state if status != "unknown" else "unknown",
            confidence=norm_confidence,
            status=status,
            signals=signals,
            evidence=evidence,
            phash=phash,
            timestamp=t0
        )

        # Auto-record high-confidence known states into persistent memory
        if self.storage and status == "known" and norm_confidence >= 0.80:
            try:
                rec_source = source
                if not rec_source:
                    if game_id == "offline_sim" or hint_state == "fixture":
                        rec_source = "synthetic_fixture"
                    else:
                        rec_source = "live"

                self.storage.record_ui_state(
                    game_id=game_id,
                    state_name=top_state,
                    phash=phash,
                    confidence=norm_confidence,
                    evidence=signals,
                    source=rec_source
                )
            except Exception as e:
                logger.debug("Auto-record state exception: %s", e)

        return result

    def _evaluate_hoi4_signals(
        self,
        frame: np.ndarray,
        candidate_scores: Dict[str, float],
        signals: Dict[str, Any],
        evidence: Dict[str, Any]
    ) -> None:
        """Evaluates visual signals for Hearts of Iron IV across 12 standard game states."""
        # Check for event popups first (highest visual priority modal overlay)
        popups = detect_popups(frame)
        if popups:
            candidate_scores["event_popup"] = 0.95
            evidence["popups_detected"] = len(popups)
            signals["modal_popup"] = True

        # Center modal OCR check for event dialog
        center_roi = NormalizedRect(0.25, 0.20, 0.50, 0.60)
        center_ocr = self.ocr_engine.read_text(frame, roi=center_roi, event_tag="state_check_center")
        center_text = center_ocr.full_text.lower()
        if "news" in center_text or "event" in center_text or "we must prevail" in center_text or "very well" in center_text:
            candidate_scores["event_popup"] = max(candidate_scores.get("event_popup", 0.0), 0.92)
            evidence["event_text"] = center_text[:80]

        # 1. Topbar presence check (top 5% of screen)
        topbar_roi = NormalizedRect(0.0, 0.0, 1.0, 0.05)
        topbar_ocr = self.ocr_engine.read_text(frame, roi=topbar_roi, event_tag="state_check_topbar")
        topbar_text = topbar_ocr.full_text

        has_topbar = bool(re.search(r"(\d+%|\d{2,4})", topbar_text)) or "150" in topbar_text or "hoi4" in topbar_text.lower()
        signals["topbar_detected"] = has_topbar

        if not has_topbar:
            # Check for Main Menu and Country Selection
            screen_ocr = self.ocr_engine.read_text(frame, roi=NormalizedRect(0.1, 0.1, 0.8, 0.8), event_tag="state_check_menu")
            menu_text = screen_ocr.full_text.lower()
            if "single player" in menu_text or "multiplayer" in menu_text or "career profile" in menu_text or "options" in menu_text:
                candidate_scores["main_menu"] = 0.95
                evidence["menu_keyword"] = "single_player/options"
            elif "select country" in menu_text or "interesting countries" in menu_text or "1936" in menu_text or "gathering storm" in menu_text:
                candidate_scores["country_selection"] = 0.95
                evidence["country_select_keyword"] = "select_country/1936"
            return

        # Topbar is present -> In-game state. Base map score.
        candidate_scores["main_map"] = 0.80

        # Check for wide National Focus Tree (spans center and right)
        focus_roi = NormalizedRect(0.20, 0.06, 0.75, 0.88)
        focus_ocr = self.ocr_engine.read_text(frame, roi=focus_roi, event_tag="state_check_focus")
        focus_text = focus_ocr.full_text.lower()
        if "national focus tree" in focus_text or "focus tree" in focus_text or "select national focus" in focus_text:
            candidate_scores["national_focus"] = 0.95
            candidate_scores["main_map"] = 0.20
            evidence["matched_keyword"] = "national_focus"
            return

        # 2. Left Menu Drawer Inspection (ROI: x=0.0-0.38, y=0.06-0.92)
        drawer_roi = NormalizedRect(0.0, 0.06, 0.38, 0.86)
        drawer_ocr = self.ocr_engine.read_text(frame, roi=drawer_roi, event_tag="state_check_drawer")
        drawer_text = drawer_ocr.full_text.lower()
        signals["drawer_text_sample"] = drawer_text[:120]

        if "laws & government" in drawer_text or "political parties" in drawer_text or "ruling party" in drawer_text or "political power" in drawer_text:
            candidate_scores["politics"] = 0.92
            candidate_scores["main_map"] = 0.30
            evidence["matched_keyword"] = "politics"
        elif "production" in drawer_text or "military factories" in drawer_text or "dockyards" in drawer_text:
            candidate_scores["production"] = 0.90
            candidate_scores["main_map"] = 0.30
            evidence["matched_keyword"] = "production"
        elif "construction" in drawer_text or "infrastructure" in drawer_text or "civilian factories" in drawer_text:
            candidate_scores["construction"] = 0.90
            candidate_scores["main_map"] = 0.30
            evidence["matched_keyword"] = "construction"
        elif "research" in drawer_text or "technology" in drawer_text or "slot" in drawer_text:
            candidate_scores["research"] = 0.90
            candidate_scores["main_map"] = 0.30
            evidence["matched_keyword"] = "research"
        elif "diplomacy" in drawer_text or "world tension" in drawer_text or "faction" in drawer_text:
            candidate_scores["diplomacy"] = 0.90
            candidate_scores["main_map"] = 0.30
            evidence["matched_keyword"] = "diplomacy"
        elif "recruit" in drawer_text or "deployment" in drawer_text or "division" in drawer_text:
            candidate_scores["recruitment"] = 0.90
            candidate_scores["main_map"] = 0.30
            evidence["matched_keyword"] = "recruitment"

        # 3. Bottom Army Group Bar (x=0.08-0.92, y=0.80-1.0)
        army_roi = NormalizedRect(0.08, 0.80, 0.84, 0.20)
        army_ocr = self.ocr_engine.read_text(frame, roi=army_roi, event_tag="state_check_army")
        army_text = army_ocr.full_text.lower()
        if "army group" in army_text or "theater" in army_text or "theatre" in army_text or "army 1" in army_text or "assigned" in army_text:
            candidate_scores["selected_army"] = 0.90
            candidate_scores["main_map"] = 0.30
            signals["army_bar_detected"] = True

    def _evaluate_sim_signals(
        self,
        frame: np.ndarray,
        candidate_scores: Dict[str, float],
        signals: Dict[str, Any],
        evidence: Dict[str, Any]
    ) -> None:
        """Evaluates visual signals for UGM Offline Strategy Simulator."""
        # Check topbar & center screen for "UGM STRATEGY SIM", "GOLD:", "PAUSED"
        top_roi = NormalizedRect(0.0, 0.0, 1.0, 0.15)
        top_ocr = self.ocr_engine.read_text(frame, roi=top_roi, event_tag="sim_state_top")
        txt = top_ocr.full_text.upper()
        signals["sim_top_text"] = txt

        if "PAUSED" in txt:
            candidate_scores["paused"] = candidate_scores.get("paused", 0.0) + 0.85
            evidence["status"] = "paused"
        elif "UGM STRATEGY" in txt or "GOLD:" in txt or "WOOD:" in txt:
            candidate_scores["main_view"] = candidate_scores.get("main_view", 0.0) + 0.80
            evidence["status"] = "active_sim"

    def _evaluate_generic_signals(
        self,
        frame: np.ndarray,
        candidate_scores: Dict[str, float],
        signals: Dict[str, Any],
        evidence: Dict[str, Any]
    ) -> None:
        """Evaluates generic signals for unsupported / unknown games."""
        full_ocr = self.ocr_engine.read_text(frame, event_tag="generic_state_scan")
        signals["text_item_count"] = len(full_ocr.items)
        if full_ocr.items:
            # Pick prominent words as potential state cues
            signals["prominent_text"] = [i.text for i in full_ocr.items[:5] if len(i.text) > 3]
