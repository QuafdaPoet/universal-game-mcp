"""
Universal Game MCP (UGM) - MCP Server Implementation
Exposes standard MCP tools for AI-driven game observation, OCR, icon detection,
safe input dispatch, and visual verification using official MCP Python SDK v2.
"""

from __future__ import annotations
import os
import sys
import json
import base64
import time
import logging
from typing import Optional, List, Dict, Any
from dataclasses import asdict
import cv2
import numpy as np
from mcp.server.mcpserver import MCPServer

from .core.types import (
    NormalizedRect,
    BoundingBox,
    WindowInfo,
    ClickResult,
    ActionResult,
    VerificationResult,
    HoverResult,
    TargetStatus,
    TargetCandidate,
    TargetResolution,
    VisionAgentEscalation,
)
from .core.safety import safety_manager, EmergencyStopTriggered, SafetyViolationError
from .core.window import WindowManager
from .core.capture import CaptureManager, FixtureBackend
from .core.ocr import RapidOCREngine, BaseOCREngine, get_ocr_engine
from .core.icon import IconDetector
from .core.input import InputController
from .core.verifier import ActionVerifier
from .core.dataset import DatasetCollector
from .core.target import TargetResolver, TargetExecutor
from .skills.loader import GameSkillPack, GameSkillPackLoader
from .adapters.hoi4.adapter import HOI4Adapter

# V1.5 Subsystems
from .memory.database import DatabaseManager
from .memory.storage import PersistentKnowledgeStorage
from .navigation.recognizer import UIStateRecognizer, UIStateDetection
from .navigation.graph import NavigationGraph, PathResult
from .navigation.navigator import UINavigator, NavigationResult
from .skills.engine import SkillExecutionEngine, SkillExecutionResult
from .skills.learner import SkillLearner
from .skills.contracts import NavigationNode, NavigationEdge
from .assets.indexer import GameAssetIndexer

# Ensure UTF-8 output on Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("ugm.server")


class UGMContext:
    """Dependency container holding game MCP subsystems."""

    def __init__(
        self,
        offline_fixture: Optional[str] = None,
        ocr_backend: str = "rapidocr",
        skills_pack_dir: Optional[str] = "game_skills/hoi4",
        db_path: Optional[str] = None
    ):
        self.window_manager = WindowManager()
        self.capture_manager = CaptureManager()
        self.ocr_engine: BaseOCREngine = get_ocr_engine(backend=ocr_backend, min_score=0.45, use_cls=False)
        self.icon_detector = IconDetector(default_threshold=0.80)
        self.input_controller = InputController(self.window_manager, dry_run_default=False)
        self.verifier = ActionVerifier(self.capture_manager, self.ocr_engine, self.input_controller)
        self.dataset_collector = DatasetCollector()
        self.pre_capture_cache: Dict[str, np.ndarray] = {}

        # Register HOI4 Adapter
        self.hoi4_adapter = HOI4Adapter()
        self.hoi4_adapter.register_icons(self.icon_detector)

        # Loaded skill packs
        self.skill_packs: Dict[str, GameSkillPack] = {}

        # Target Resolver & Executor
        self.target_resolver = TargetResolver(
            self.icon_detector,
            self.ocr_engine,
            self.verifier,
            skill_packs=self.skill_packs
        )
        self.target_executor = TargetExecutor(
            self.target_resolver,
            self.capture_manager,
            self.input_controller,
            self.verifier
        )

        if skills_pack_dir and os.path.isdir(skills_pack_dir):
            try:
                pack = GameSkillPackLoader.load_pack(skills_pack_dir)
                self.skill_packs[pack.game_id] = pack
                self.target_resolver.register_skill_pack(pack.game_id, pack)
                for sel_id, sel_def in pack.selectors.items():
                    if sel_def.template_file:
                        tmpl_path = pack.get_template_path(sel_def.template_file)
                        if os.path.isfile(tmpl_path):
                            try:
                                self.icon_detector.load_template_file(sel_id, tmpl_path)
                            except Exception:
                                pass
            except Exception as e:
                logger.debug("Skill pack autoload notice for %s: %s", skills_pack_dir, e)

        # V1.5 Persistent Memory & Knowledge Storage
        self.storage = PersistentKnowledgeStorage(DatabaseManager(db_path or "data/memory/ugm_memory.db"))
        self._init_default_game_metadata()

        # V1.5 UI State Recognizer
        self.state_recognizer = UIStateRecognizer(
            ocr_engine=self.ocr_engine,
            icon_detector=self.icon_detector,
            storage=self.storage
        )

        # V1.5 Navigation Graphs & Navigators
        self.navigation_graphs: Dict[str, NavigationGraph] = {}
        self.navigators: Dict[str, UINavigator] = {}

        # V1.5 Skill Execution Engine & Learner
        self.skill_engine = SkillExecutionEngine(
            capture_manager=self.capture_manager,
            input_controller=self.input_controller,
            target_resolver=self.target_resolver,
            target_executor=self.target_executor,
            verifier=self.verifier,
            ocr_engine=self.ocr_engine,
            storage=self.storage,
            skill_packs=self.skill_packs
        )
        self.skill_engine.state_recognizer = self.state_recognizer
        self.skill_learner = SkillLearner(
            storage=self.storage,
            execution_engine=self.skill_engine
        )

        # V1.5 Asset Indexer
        self.asset_indexer = GameAssetIndexer()

        if offline_fixture and os.path.exists(offline_fixture):
            self.setup_offline_fixture(offline_fixture)

    def _init_default_game_metadata(self) -> None:
        try:
            self.storage.register_game(
                game_id="hoi4",
                name="Hearts of Iron IV",
                version="1.0.0",
                base_res=(1920, 1080)
            )
            self.storage.register_game(
                game_id="offline_sim",
                name="UGM Offline Strategy Simulator",
                version="1.0.0",
                base_res=(900, 550)
            )
        except Exception as e:
            logger.debug("Default game metadata notice: %s", e)

    def get_navigation_graph(self, game_id: str = "hoi4") -> NavigationGraph:
        if game_id not in self.navigation_graphs:
            graph = NavigationGraph(game_id=game_id, storage=self.storage)
            if game_id == "hoi4":
                self._populate_hoi4_navigation_graph(graph)
            elif game_id == "offline_sim":
                self._populate_sim_navigation_graph(graph)
            self.navigation_graphs[game_id] = graph
        return self.navigation_graphs[game_id]

    def get_navigator(self, game_id: str = "hoi4") -> UINavigator:
        if game_id not in self.navigators:
            graph = self.get_navigation_graph(game_id)
            nav = UINavigator(
                recognizer=self.state_recognizer,
                graph=graph,
                capture_manager=self.capture_manager,
                input_controller=self.input_controller,
                target_executor=self.target_executor,
                target_resolver=self.target_resolver
            )
            self.navigators[game_id] = nav
        return self.navigators[game_id]

    def _populate_hoi4_navigation_graph(self, graph: NavigationGraph) -> None:
        nodes = [
            "main_menu",
            "country_selection",
            "main_map",
            "politics",
            "research",
            "production",
            "construction",
            "national_focus",
            "diplomacy",
            "recruitment",
            "selected_army",
            "event_popup",
        ]
        for nid in nodes:
            graph.register_node(NavigationNode(node_id=nid, menu_name=nid.replace("_", " ").title(), game_id="hoi4", identification_selector=""))

        # Add bidirectional transitions between states
        transitions = [
            # Main Menu & Setup
            ("main_menu", "country_selection", "click", {"selector": "single_player"}),
            ("country_selection", "main_map", "click", {"selector": "start_game"}),

            # Politics & Focus Tree
            ("main_map", "politics", "press_keys", {"keys": ["shift", "q"]}),
            ("politics", "main_map", "press_keys", {"keys": ["escape"]}),
            ("politics", "national_focus", "click", {"selector": "national_focus_slot"}),
            ("national_focus", "politics", "press_keys", {"keys": ["escape"]}),
            ("national_focus", "main_map", "press_keys", {"keys": ["escape"]}),

            # Topbar tabs
            ("main_map", "production", "press_keys", {"keys": ["y"]}),
            ("production", "main_map", "press_keys", {"keys": ["escape"]}),
            ("main_map", "construction", "press_keys", {"keys": ["t"]}),
            ("construction", "main_map", "press_keys", {"keys": ["escape"]}),
            ("main_map", "research", "press_keys", {"keys": ["w"]}),
            ("research", "main_map", "press_keys", {"keys": ["escape"]}),
            ("main_map", "diplomacy", "press_keys", {"keys": ["e"]}),
            ("diplomacy", "main_map", "press_keys", {"keys": ["escape"]}),
            ("main_map", "recruitment", "press_keys", {"keys": ["u"]}),
            ("recruitment", "main_map", "press_keys", {"keys": ["escape"]}),

            # Military selection
            ("main_map", "selected_army", "click", {"selector": "army_group_leader"}),
            ("selected_army", "main_map", "press_keys", {"keys": ["escape"]}),

            # Modal dialog / Event dismissal
            ("event_popup", "main_map", "click", {"selector": "event_popup_option"}),
            ("event_popup", "main_map", "press_keys", {"keys": ["enter"]}),
        ]
        for src, tgt, act, params in transitions:
            graph.register_edge(NavigationEdge(source_node_id=src, target_node_id=tgt, trigger_action=act, parameters=params, cost=1.0), persist=False)

    def _populate_sim_navigation_graph(self, graph: NavigationGraph) -> None:
        graph.register_node(NavigationNode(node_id="main_view", menu_name="Main View", game_id="offline_sim", identification_selector=""))
        graph.register_node(NavigationNode(node_id="paused", menu_name="Paused", game_id="offline_sim", identification_selector=""))
        graph.register_edge(NavigationEdge("main_view", "paused", "press_keys", {"keys": ["space"]}, cost=1.0), persist=False)
        graph.register_edge(NavigationEdge("paused", "main_view", "press_keys", {"keys": ["space"]}, cost=1.0), persist=False)

    def setup_offline_fixture(self, fixture_path: str) -> None:
        """Sets up a mock game window and fixture image for offline operation."""
        img = cv2.imread(fixture_path)
        if img is None:
            raise ValueError(f"Could not load fixture from {fixture_path}")
        h, w = img.shape[:2]
        mock_win = WindowInfo(
            hwnd=9999,
            title="Hearts of Iron IV (Mock Fixture)",
            process_name="hoi4.exe",
            pid=1234,
            x=0,
            y=0,
            width=w,
            height=h,
            client_x=0,
            client_y=0,
            client_width=w,
            client_height=h,
            is_minimized=False,
            is_focused=True,
            dpi_scale=1.0
        )
        self.window_manager.set_mock_window(mock_win)
        fixture_backend = FixtureBackend(img)
        self.capture_manager.set_backend(fixture_backend)
        logger.info("UGM configured in offline fixture mode using %s (%dx%d)", fixture_path, w, h)


def create_server(context: Optional[UGMContext] = None) -> MCPServer:
    """Creates and configures the MCP server with the 10 standard UGM tools."""
    ctx = context or UGMContext()
    server = MCPServer(
        name="universal-game-mcp",
        version="1.5.0",
        instructions="Universal Game MCP (UGM) allows AI agents to observe, read text, locate icons, interact safely, navigate menus, execute game skills, and learn verified procedural knowledge across game sessions."
    )

    # 1. get_game_window
    @server.tool()
    def get_game_window(
        window_title: Optional[str] = "Hearts of Iron IV",
        process_name: Optional[str] = "hoi4.exe"
    ) -> Dict[str, Any]:
        """
        Locates and attaches to the target game window.
        Returns window handle, geometry, client rectangle, and focus status.
        """
        win = ctx.window_manager.find_window(window_title=window_title, process_name=process_name)
        if win is None:
            return {
                "found": False,
                "message": f"Could not find window matching title='{window_title}' or process='{process_name}'",
                "window": None
            }
        return {
            "found": True,
            "window": win.to_dict()
        }

    # 2. capture_screen
    @server.tool()
    def capture_screen(
        roi: Optional[List[float]] = None,
        save_path: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Captures the current screen of the active game window.
        Optional 'roi' parameter specifies [x, y, width, height] normalized coordinates (0.0 to 1.0).
        """
        norm_roi = NormalizedRect(roi[0], roi[1], roi[2], roi[3]) if roi and len(roi) == 4 else None
        frame = ctx.capture_manager.capture(window=ctx.window_manager.current_window, roi=norm_roi)

        h, w = frame.shape[:2]
        saved_file = None
        if save_path:
            saved_file = ctx.capture_manager.save_image(frame, save_path)

        preview_b64 = ctx.capture_manager.encode_base64_png(frame)
        cap_id = f"cap_{int(time.time() * 1000)}"
        ctx.pre_capture_cache[cap_id] = frame

        # Keep cache bounded
        if len(ctx.pre_capture_cache) > 10:
            oldest = next(iter(ctx.pre_capture_cache))
            del ctx.pre_capture_cache[oldest]

        return {
            "capture_id": cap_id,
            "width": w,
            "height": h,
            "latency_ms": round(ctx.capture_manager.last_latency_ms, 2),
            "saved_file": saved_file,
            "image_base64": preview_b64,
        }

    # 3. read_text
    @server.tool()
    def read_text(
        roi: Optional[List[float]] = None,
        event_tag: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Performs event-driven OCR on the game screen or ROI using RapidOCR (PP-OCRv4/v6 CPU).
        Returns bounding boxes normalized relative to the game window and detected text items.
        """
        norm_roi = NormalizedRect(roi[0], roi[1], roi[2], roi[3]) if roi and len(roi) == 4 else None
        frame = ctx.capture_manager.capture(window=ctx.window_manager.current_window)
        ocr_res = ctx.ocr_engine.read_text(frame, roi=norm_roi, event_tag=event_tag)
        return ocr_res.to_dict()

    # 4. locate_ui_element
    @server.tool()
    def locate_ui_element(
        selector_id: Optional[str] = None,
        icon_name: Optional[str] = None,
        template_base64: Optional[str] = None,
        text_query: Optional[str] = None,
        roi: Optional[List[float]] = None,
        threshold: float = 0.80,
        score_tolerance: float = 0.05,
        hover_verification: bool = False,
        expected_tooltip: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Locates a UI element via template matching, OCR text query, or registered selector.
        Detects ambiguity and generates external Vision Agent escalation payload when uncertain.
        """
        t0 = time.perf_counter()
        norm_roi = NormalizedRect(roi[0], roi[1], roi[2], roi[3]) if roi and len(roi) == 4 else None
        frame = ctx.capture_manager.capture(window=ctx.window_manager.current_window)

        custom_img = None
        if template_base64:
            raw_bytes = base64.b64decode(template_base64)
            nparr = np.frombuffer(raw_bytes, np.uint8)
            custom_img = cv2.imdecode(nparr, cv2.IMREAD_UNCHANGED)

        resolved_icon_name = icon_name
        resolved_text_pattern = text_query
        resolved_roi = norm_roi
        resolved_threshold = threshold
        resolved_hover = hover_verification
        resolved_tooltip = expected_tooltip

        if selector_id:
            for pack in ctx.skill_packs.values():
                if selector_id in pack.selectors:
                    sel = pack.selectors[selector_id]
                    if not resolved_icon_name and sel.template_file:
                        resolved_icon_name = selector_id
                    if not resolved_text_pattern and sel.text_pattern:
                        resolved_text_pattern = sel.text_pattern
                    if not resolved_roi and sel.search_roi:
                        resolved_roi = sel.search_roi
                    resolved_threshold = sel.threshold
                    resolved_hover = sel.hover_verification
                    resolved_tooltip = sel.expected_tooltip_contains
                    break

        resolution = ctx.target_resolver.resolve_target(
            frame=frame,
            icon_name=resolved_icon_name,
            template_image=custom_img,
            text_pattern=resolved_text_pattern,
            search_roi=resolved_roi,
            threshold=resolved_threshold,
            score_tolerance=score_tolerance,
            hover_verification=resolved_hover,
            expected_tooltip=resolved_tooltip,
            selector_id=selector_id
        )

        res_dict = resolution.to_dict()
        res_dict["latency_ms"] = round((time.perf_counter() - t0) * 1000.0, 2)
        res_dict["threshold"] = resolved_threshold
        res_dict["roi_normalized"] = asdict(resolved_roi) if resolved_roi else None
        res_dict["count"] = len(resolution.all_candidates)
        res_dict["items"] = [c.to_dict() for c in resolution.all_candidates]
        res_dict["is_ambiguous"] = (resolution.status == TargetStatus.AMBIGUOUS.value)
        return res_dict

    # 4b. locate_icon (Backward compatible adapter)
    @server.tool()
    def locate_icon(
        icon_name: Optional[str] = None,
        template_base64: Optional[str] = None,
        roi: Optional[List[float]] = None,
        threshold: float = 0.80
    ) -> Dict[str, Any]:
        """
        Locates occurrences of an icon in the game window using OpenCV template matching with NMS.
        Returns normalized bounding boxes, center coordinates, and flags ambiguity if multiple matches compete.
        """
        return locate_ui_element(
            icon_name=icon_name,
            template_base64=template_base64,
            roi=roi,
            threshold=threshold
        )

    # 5. hover_target
    @server.tool()
    def hover_target(
        x: float,
        y: float,
        duration_ms: int = 500,
        read_tooltip: bool = True,
        dry_run: bool = False,
        timeout_s: float = 5.0
    ) -> Dict[str, Any]:
        """
        Moves the mouse cursor to normalized coordinates (x, y) [0.0 - 1.0] and hovers.
        If read_tooltip is True, isolates the popup tooltip and reads its text via OCR.
        """
        if read_tooltip:
            hover_res = ctx.verifier.hover_and_read_tooltip(
                norm_x=x,
                norm_y=y,
                duration_ms=duration_ms,
                dry_run=dry_run
            )
            return hover_res.to_dict()
        else:
            t0 = time.perf_counter()
            screen_x, screen_y, executed = ctx.input_controller.hover(
                norm_x=x,
                norm_y=y,
                duration_ms=duration_ms,
                dry_run=dry_run
            )
            return {
                "target_normalized": [x, y],
                "target_pixel": [screen_x, screen_y],
                "duration_ms": duration_ms,
                "tooltip_detected": False,
                "tooltip_text": None,
                "latency_ms": round((time.perf_counter() - t0) * 1000.0, 2)
            }

    # 6. click_target
    @server.tool()
    def click_target(
        x: Optional[float] = None,
        y: Optional[float] = None,
        selector_id: Optional[str] = None,
        text_query: Optional[str] = None,
        icon_name: Optional[str] = None,
        button: str = "left",
        clicks: int = 1,
        dry_run: bool = False,
        verify: bool = True,
        expected_text: Optional[str] = None,
        verification_roi: Optional[List[float]] = None,
        min_diff_ratio: float = 0.005,
        timeout_s: float = 5.0
    ) -> Dict[str, Any]:
        """
        Safely clicks at normalized coordinates (x, y) or dynamically resolves target selector/query.
        Always verifies visual state change; never reports success solely because a mouse click was dispatched.
        """
        norm_ver_roi = NormalizedRect(
            verification_roi[0], verification_roi[1], verification_roi[2], verification_roi[3]
        ) if verification_roi and len(verification_roi) == 4 else None

        target_resolution = None
        if (x is None or y is None) and (selector_id or text_query or icon_name):
            resolved_icon_name = icon_name
            resolved_text_pattern = text_query
            resolved_roi = norm_ver_roi
            resolved_threshold = 0.80
            resolved_hover = False
            resolved_tooltip = None

            if selector_id:
                for pack in ctx.skill_packs.values():
                    if selector_id in pack.selectors:
                        sel = pack.selectors[selector_id]
                        if not resolved_icon_name and sel.template_file:
                            resolved_icon_name = selector_id
                        if not resolved_text_pattern and sel.text_pattern:
                            resolved_text_pattern = sel.text_pattern
                        if not resolved_roi and sel.search_roi:
                            resolved_roi = sel.search_roi
                        resolved_threshold = sel.threshold
                        resolved_hover = sel.hover_verification
                        resolved_tooltip = sel.expected_tooltip_contains
                        break

            frame = ctx.capture_manager.capture(window=ctx.window_manager.current_window)
            target_resolution = ctx.target_resolver.resolve_target(
                frame=frame,
                icon_name=resolved_icon_name,
                text_pattern=resolved_text_pattern,
                search_roi=resolved_roi,
                threshold=resolved_threshold,
                hover_verification=resolved_hover,
                expected_tooltip=resolved_tooltip,
                selector_id=selector_id,
                dry_run=dry_run
            )
            if norm_ver_roi is None and resolved_roi:
                norm_ver_roi = resolved_roi

        click_res = ctx.target_executor.click_and_verify(
            norm_x=x,
            norm_y=y,
            target_resolution=target_resolution,
            button=button,
            clicks=clicks,
            dry_run=dry_run,
            expected_text=expected_text,
            verification_roi=norm_ver_roi,
            min_diff_ratio=min_diff_ratio if verify else 0.0,
            timeout_s=timeout_s
        )
        return click_res.to_dict()

    # 7. press_keys
    @server.tool()
    def press_keys(
        keys: List[str],
        duration_ms: int = 100,
        dry_run: bool = False,
        timeout_s: float = 5.0
    ) -> Dict[str, Any]:
        """
        Dispatches keystrokes to the target game window (e.g. ['space'] to pause/unpause HOI4).
        """
        res = ctx.input_controller.press_keys(
            keys=keys,
            duration_ms=duration_ms,
            dry_run=dry_run
        )
        return res.to_dict()

    # 8. verify_state
    @server.tool()
    def verify_state(
        expected_text: Optional[str] = None,
        roi: Optional[List[float]] = None,
        pre_capture_id: Optional[str] = None,
        min_diff_ratio: float = 0.005
    ) -> Dict[str, Any]:
        """
        Verifies visual game state comparing against pre_capture_id or baseline frame.
        Optionally validates that expected_text appears in the target ROI.
        """
        norm_roi = NormalizedRect(roi[0], roi[1], roi[2], roi[3]) if roi and len(roi) == 4 else None
        post_frame = ctx.capture_manager.capture(window=ctx.window_manager.current_window)

        pre_frame = None
        if pre_capture_id is not None:
            if pre_capture_id not in ctx.pre_capture_cache:
                return {
                    "verified": False,
                    "diff_ratio": 0.0,
                    "message": f"Verification failed: pre_capture_id '{pre_capture_id}' was not found in cache or has expired.",
                    "latency_ms": 0.0,
                    "details": {"error": "invalid_pre_capture_id"}
                }
            pre_frame = ctx.pre_capture_cache[pre_capture_id]
        else:
            if expected_text is None and min_diff_ratio > 0.0:
                return {
                    "verified": False,
                    "diff_ratio": 0.0,
                    "message": "Verification failed: no baseline pre_capture_id provided for visual diff verification.",
                    "latency_ms": 0.0,
                    "details": {"error": "missing_baseline"}
                }
            pre_frame = post_frame

        ver_res = ctx.verifier.verify_selection(
            pre_frame=pre_frame,
            post_frame=post_frame,
            roi=norm_roi,
            expected_text=expected_text,
            min_diff_ratio=min_diff_ratio
        )
        return ver_res.to_dict()

    # 8b. verify_selection (Backward compatible adapter)
    @server.tool()
    def verify_selection(
        expected_text: Optional[str] = None,
        roi: Optional[List[float]] = None,
        pre_capture_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Verifies visual state change comparing against pre_capture_id or baseline frame.
        Optionally validates that expected_text appears in the target ROI.
        """
        return verify_state(
            expected_text=expected_text,
            roi=roi,
            pre_capture_id=pre_capture_id
        )

    # 9. emergency_stop
    @server.tool()
    def emergency_stop(
        reason: str = "User requested emergency stop"
    ) -> Dict[str, Any]:
        """
        Immediately halts all inputs and sets the global killswitch.
        Subsequent clicks and keystrokes will be blocked until reset.
        """
        return safety_manager.trigger_emergency_stop(reason=reason)

    # 10. save_training_sample
    @server.tool()
    def save_training_sample(
        tag: str,
        roi: Optional[List[float]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        force: bool = False
    ) -> Dict[str, Any]:
        """
        Opt-in saving of game frame annotations for dataset collection.
        Disabled by default to protect privacy. Stored locally and excluded from git.
        """
        norm_roi = NormalizedRect(roi[0], roi[1], roi[2], roi[3]) if roi and len(roi) == 4 else None
        frame = ctx.capture_manager.capture(window=ctx.window_manager.current_window)
        return ctx.dataset_collector.save_sample(
            image=frame,
            tag=tag,
            roi=norm_roi,
            metadata=metadata,
            force=force
        )

    # 11. list_game_packs
    @server.tool()
    def list_game_packs() -> Dict[str, Any]:
        """
        Discovers and lists all installed curated Game Skill Packs and registered games in persistent memory.
        """
        packs_out = []
        for gid, pack in ctx.skill_packs.items():
            packs_out.append({
                "game_id": gid,
                "name": pack.manifest.name,
                "version": pack.manifest.version,
                "description": pack.manifest.description,
                "is_curated": True,
                "selectors_count": len(pack.selectors),
                "skills_count": len(pack.skills),
                "skills": list(pack.skills.keys()),
                "selectors": list(pack.selectors.keys())
            })
        for g in ctx.storage.list_games():
            gid = g["game_id"]
            if gid not in ctx.skill_packs:
                learned_skills = ctx.storage.list_learned_skills(game_id=gid)
                packs_out.append({
                    "game_id": gid,
                    "name": g["name"],
                    "version": g["version"],
                    "description": "Dynamic game registered via learned knowledge",
                    "is_curated": False,
                    "selectors_count": 0,
                    "skills_count": len(learned_skills),
                    "skills": [s.skill_id for s in learned_skills],
                    "selectors": []
                })
        return {
            "count": len(packs_out),
            "packs": packs_out
        }

    # 12. get_ui_state
    @server.tool()
    def get_ui_state(
        game_id: str = "hoi4",
        hint_state: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Classifies and reports the current game interface state (e.g. main_map, production, research, paused).
        Returns confidence, supporting observable evidence, perceptual hash, and whether state is known/ambiguous/unknown.
        """
        frame = ctx.capture_manager.capture(window=ctx.window_manager.current_window)
        det = ctx.state_recognizer.recognize_state(frame, game_id=game_id, hint_state=hint_state)
        return det.to_dict()

    # 13. memory_search
    @server.tool()
    def memory_search(
        game_id: str = "hoi4",
        query: Optional[str] = None,
        search_type: str = "all",
        limit: int = 20
    ) -> Dict[str, Any]:
        """
        Retrieves relevant remembered experience from the persistent SQLite knowledge store.
        Can search 'states', 'transitions', 'skills', 'actions', or 'all'.
        """
        results: Dict[str, Any] = {"game_id": game_id, "search_type": search_type}
        if search_type in ("all", "states"):
            states = ctx.storage.get_states(game_id)
            if query:
                states = [s for s in states if query.lower() in s.state_name.lower()]
            results["states"] = [asdict(s) for s in states[:limit]]
        if search_type in ("all", "transitions"):
            transitions = ctx.storage.get_transitions(game_id)
            if query:
                transitions = [t for t in transitions if query.lower() in t.from_state.lower() or query.lower() in t.to_state.lower()]
            results["transitions"] = [asdict(t) for t in transitions[:limit]]
        if search_type in ("all", "skills"):
            skills = ctx.storage.list_learned_skills(game_id=game_id)
            if query:
                skills = [s for s in skills if query.lower() in s.skill_id.lower() or query.lower() in s.name.lower()]
            results["learned_skills"] = [asdict(s) for s in skills[:limit]]
        if search_type in ("all", "actions"):
            actions = ctx.storage.get_action_history(game_id=game_id, limit=limit)
            results["action_history"] = actions
        return results

    # 14. get_navigation_plan
    @server.tool()
    def get_navigation_plan(
        target_state: str,
        start_state: Optional[str] = None,
        game_id: str = "hoi4"
    ) -> Dict[str, Any]:
        """
        Calculates optimal multi-step navigation route to destination UI state using Dijkstra pathfinding.
        If start_state is omitted, live UI recognition identifies current starting position.
        """
        if not start_state:
            frame = ctx.capture_manager.capture(window=ctx.window_manager.current_window)
            det = ctx.state_recognizer.recognize_state(frame, game_id=game_id)
            start_state = det.state_name

        graph = ctx.get_navigation_graph(game_id)
        plan = graph.plan_route(start_state=start_state, goal_state=target_state)
        return {
            "found": plan.found,
            "start_state": plan.start_state,
            "target_state": plan.target_state,
            "total_cost": plan.total_cost,
            "steps_count": len(plan.edges),
            "nodes_path": plan.nodes,
            "steps": [asdict(e) for e in plan.edges],
            "message": plan.message
        }

    # 15. navigate_to
    @server.tool()
    def navigate_to(
        target_state: str,
        game_id: str = "hoi4",
        max_retries: int = 2,
        dry_run: bool = False
    ) -> Dict[str, Any]:
        """
        Safely navigates the game interface from current state to target destination state.
        Verifies resulting state after every single step and replans or halts safely on failure.
        """
        navigator = ctx.get_navigator(game_id)
        res = navigator.navigate_to(
            target_state=target_state,
            game_id=game_id,
            max_retries=max_retries,
            dry_run=dry_run
        )
        return res.to_dict()

    # 16. execute_skill
    @server.tool()
    def execute_skill(
        skill_id: str,
        game_id: Optional[str] = "hoi4",
        params: Optional[Dict[str, Any]] = None,
        dry_run: bool = False
    ) -> Dict[str, Any]:
        """
        Executes a registered curated or verified learned Game Skill.
        Validates preconditions, dispatches sequential allowlisted primitives, verifies postconditions.
        Never reports success unless postcondition verification passed.
        """
        res = ctx.skill_engine.execute_skill(
            skill_id=skill_id,
            game_id=game_id,
            params=params,
            dry_run=dry_run
        )
        return res.to_dict()

    # 17. learn_skill
    @server.tool()
    def learn_skill(
        skill_id: str,
        name: str,
        steps: List[Dict[str, Any]],
        game_id: str = "hoi4",
        description: str = "",
        preconditions: Optional[Dict[str, Any]] = None,
        post_verification: Optional[Dict[str, Any]] = None,
        source: str = "live"
    ) -> Dict[str, Any]:
        """
        Saves a newly discovered action sequence as a candidate reusable Game Skill in persistent memory.
        Enforces allowlisted primitives, semantic selectors, and protects curated Game Skill Packs.
        """
        try:
            res = ctx.skill_learner.learn_candidate_skill(
                game_id=game_id,
                skill_id=skill_id,
                name=name,
                steps=steps,
                description=description,
                preconditions=preconditions,
                post_verification=post_verification,
                source=source
            )
            res["success"] = True
            return res
        except Exception as e:
            return {
                "success": False,
                "status": "rejected",
                "skill_id": skill_id,
                "message": f"Skill learning rejected: {e}"
            }

    # 18. list_learned_skills
    @server.tool()
    def list_learned_skills(
        game_id: Optional[str] = None,
        status: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Lists all procedurally learned skills, their validation track records, and status (candidate/verified/rejected).
        """
        skills = ctx.skill_learner.list_learned_skills(game_id=game_id, status=status)
        return {
            "count": len(skills),
            "skills": skills
        }

    # 19. get_learning_status
    @server.tool()
    def get_learning_status(
        game_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Reports overall persistent memory statistics: state counts, transitions, action history, and learned skills.
        """
        return ctx.skill_learner.get_learning_status(game_id=game_id)

    # 20. delete_learned_memory
    @server.tool()
    def delete_learned_memory(
        game_id: Optional[str] = None,
        skill_id: Optional[str] = None,
        scope: str = "all"
    ) -> Dict[str, Any]:
        """
        Safely deletes learned experience from persistent storage.
        Can delete an individual skill by skill_id, or scoped by game_id and scope ('all', 'history', 'skills', 'transitions', 'states').
        """
        if skill_id:
            deleted = ctx.skill_learner.delete_skill(skill_id)
            return {
                "deleted": deleted,
                "skill_id": skill_id,
                "message": f"Skill '{skill_id}' deleted." if deleted else f"Skill '{skill_id}' not found."
            }
        else:
            return ctx.storage.delete_memory(game_id=game_id, scope=scope)

    # 21. index_game_assets
    @server.tool()
    def index_game_assets(
        game_path: Optional[str] = None,
        game_id: str = "hoi4"
    ) -> Dict[str, Any]:
        """
        Extracts read-only UI metadata, sprite definitions, and localization keys from installed game files into local index cache.
        If game_path is omitted, auto-discovers the installed HOI4 directory. Read-only; never alters game files or memory.
        """
        return ctx.asset_indexer.index_game_directory(game_id=game_id, game_path=game_path)

    # 22. hoi4_get_knowledge
    @server.tool()
    def hoi4_get_knowledge(
        topic: str,
        subtopic: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Retrieves prebuilt HOI4 campaign knowledge, operating loops, strategic principles, and country playbooks on-demand.
        Topics: 'meta', 'rules_and_modes', 'operating_loop', 'strategy_core', 'country' (subtopics: 'germany_fair', 'china_fair', 'poland_meta').
        """
        return ctx.hoi4_adapter.get_knowledge(topic=topic, subtopic=subtopic)

    # 23. hoi4_detect_environment
    @server.tool()
    def hoi4_detect_environment(
        custom_game_path: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Discovers local HOI4 installations across Steam libraries, checks running process status, and verifies OCR readiness.
        """
        return ctx.hoi4_adapter.detect_environment(custom_game_path=custom_game_path)

    # 24. hoi4_read_topbar
    @server.tool()
    def hoi4_read_topbar(
        detailed: bool = True
    ) -> Dict[str, Any]:
        """
        Performs event-driven OCR across HOI4 topbar resources (political power, stability, war support, manpower, factories).
        If detailed=True, additionally extracts parsed date, pause status, and simulation speed.
        """
        frame = ctx.capture_manager.capture_frame(target_process="hoi4.exe")
        if frame is None:
            return {"success": False, "message": "Failed to capture screen frame for HOI4 topbar reading"}
        metrics = ctx.hoi4_adapter.parse_topbar(ctx.ocr_engine, frame, detailed=detailed)
        return {
            "success": True,
            "metrics": metrics,
            "timestamp": time.time()
        }

    # 25. hoi4_read_tooltip
    @server.tool()
    def hoi4_read_tooltip(
        target: Optional[str] = None,
        x: Optional[int] = None,
        y: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Hovers over target selector or coordinate, detects differential tooltip bounding box, and extracts tooltip text via OCR.
        """
        before_frame = ctx.capture_manager.capture_frame(target_process="hoi4.exe")

        # Determine hover coordinates
        cx, cy = 960, 540
        if target:
            res = ctx.target_resolver.resolve(target, frame=before_frame, game_id="hoi4")
            if res.is_resolved:
                cx, cy = res.coordinates_pixel
        elif x is not None and y is not None:
            cx, cy = x, y

        # Dispatch safe hover action
        ctx.input_controller.move_mouse(cx, cy)
        time.sleep(0.35)  # Allow tooltip fade-in
        after_frame = ctx.capture_manager.capture_frame(target_process="hoi4.exe")

        if before_frame is None or after_frame is None:
            return {"success": False, "message": "Failed to capture before/after frames for tooltip detection"}

        box = ctx.hoi4_adapter.detect_tooltip(before_frame, after_frame, cursor_xy=(cx, cy))
        if not box:
            return {
                "success": False,
                "cursor": [cx, cy],
                "message": "No tooltip appearance detected via differential analysis"
            }

        bx, by, bw, bh = box
        H, W = after_frame.shape[:2]
        roi = NormalizedRect(x=bx / W, y=by / H, width=bw / W, height=bh / H)
        ocr_res = ctx.ocr_engine.read_text(after_frame, roi=roi, event_tag="hoi4_tooltip_ocr")

        return {
            "success": True,
            "cursor": [cx, cy],
            "box": box,
            "roi": roi.to_dict(),
            "text": ocr_res.full_text,
            "confidence": max([i.confidence for i in ocr_res.items], default=0.0)
        }

    # 26. hoi4_detect_popups
    @server.tool()
    def hoi4_detect_popups() -> Dict[str, Any]:
        """
        Scans active game view for modal country events or newspaper popups using paper mask segmentation.
        """
        frame = ctx.capture_manager.capture_frame(target_process="hoi4.exe")
        if frame is None:
            return {"success": False, "message": "Failed to capture screen frame for popup detection"}
        popups = ctx.hoi4_adapter.detect_popups(frame)
        return {
            "success": True,
            "popups_count": len(popups),
            "popups": popups
        }

    return server


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Universal Game MCP Server (UGM)")
    parser.add_argument("--fixture", type=str, default=None, help="Path to offline fixture image for testing")
    parser.add_argument("--dry-run", action="store_true", help="Enforce dry-run mode for all input actions")
    args = parser.parse_args()

    ctx = UGMContext(offline_fixture=args.fixture)
    if args.dry_run:
        safety_manager.set_dry_run_enforced(True)
        ctx.input_controller.dry_run_default = True

    server = create_server(ctx)
    logger.info("Starting Universal Game MCP (UGM) server via stdio transport...")
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
