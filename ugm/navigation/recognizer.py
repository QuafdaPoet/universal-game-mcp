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
        hint_state: Optional[str] = None
    ) -> UIStateDetection:
        """
        Classifies the active UI state from a video frame.
        """
        t0 = time.time()
        phash = compute_dhash(frame)
        signals: Dict[str, Any] = {}
        candidate_scores: Dict[str, float] = {}
        evidence: Dict[str, Any] = {"phash": phash, "game_id": game_id}

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
                self.storage.record_ui_state(
                    game_id=game_id,
                    state_name=top_state,
                    phash=phash,
                    confidence=norm_confidence,
                    evidence=signals,
                    source="live"
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
        """Evaluates visual signals for Hearts of Iron IV."""
        # 1. Topbar presence check (top 5% of screen)
        topbar_roi = NormalizedRect(0.0, 0.0, 1.0, 0.05)
        topbar_ocr = self.ocr_engine.read_text(frame, roi=topbar_roi, event_tag="state_check_topbar")
        topbar_text = topbar_ocr.full_text

        has_topbar = bool(re.search(r"(\d+%|\d{2,4})", topbar_text)) or "150" in topbar_text or "hoi4" in topbar_text.lower()
        signals["topbar_detected"] = has_topbar
        if has_topbar:
            candidate_scores["main_map"] = 0.80

        # 2. Left Menu Drawer Inspection (ROI: x=0.0-0.35, y=0.08-0.90)
        drawer_roi = NormalizedRect(0.0, 0.08, 0.35, 0.82)
        drawer_ocr = self.ocr_engine.read_text(frame, roi=drawer_roi, event_tag="state_check_drawer")
        drawer_text = drawer_ocr.full_text.lower()
        signals["drawer_text_sample"] = drawer_text[:120]

        if "production" in drawer_text or "military factories" in drawer_text or "dockyards" in drawer_text:
            candidate_scores["production"] = 0.90
            candidate_scores["main_map"] = 0.30
            evidence["matched_keyword"] = "production"
        elif "construction" in drawer_text or "infrastructure" in drawer_text:
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

        # 3. Bottom Army Group Bar (x=0.10-0.90, y=0.82-1.0)
        army_roi = NormalizedRect(0.10, 0.82, 0.80, 0.18)
        army_ocr = self.ocr_engine.read_text(frame, roi=army_roi, event_tag="state_check_army")
        if "army group" in army_ocr.full_text.lower() or "theater" in army_ocr.full_text.lower():
            candidate_scores["army_selected"] = candidate_scores.get("army_selected", 0.0) + 0.55
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
        """Evaluates generic signals for unsupported / unknown games using adaptive layout analysis."""
        try:
            full_ocr = self.ocr_engine.read_text(frame, event_tag="generic_state_scan")
            signals["text_item_count"] = len(full_ocr.items)
            prominent = [i.text.strip() for i in full_ocr.items[:8] if len(i.text.strip()) > 2]
            signals["prominent_text"] = prominent
            evidence["prominent_text"] = prominent

            # Match against registered states in persistent storage
            if self.storage and prominent:
                game_id = evidence.get("game_id", "generic")
                known_states = self.storage.get_states(game_id)
                for ks in known_states:
                    k_ev = ks.evidence or {}
                    k_prom = k_ev.get("prominent_text", [])
                    if k_prom:
                        # Overlap ratio: count how many anchor keywords from known state (k_prom) are found in observed text
                        matches = sum(1 for kp in k_prom if any(kp.lower() in p.lower() or p.lower() in kp.lower() for p in prominent))
                        overlap_ratio = matches / max(1, len(k_prom))
                        if overlap_ratio >= 0.5:
                            score = round(0.5 + (overlap_ratio * 0.4), 3)
                            candidate_scores[ks.state_name] = max(candidate_scores.get(ks.state_name, 0.0), score)
                            signals["anchor_match"] = {"state": ks.state_name, "score": score, "matches": matches}
        except Exception as e:
            logger.debug("Generic state scan notice: %s", e)
