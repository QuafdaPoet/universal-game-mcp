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
from typing import Optional, List, Dict, Any, Annotated
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4
import cv2
import numpy as np
from mcp.server.mcpserver import MCPServer
from pydantic import Field

from . import __version__
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
from .core.window import WindowManager, game_id_for_window
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
from .memory.database import DatabaseManager, SIMULATION_DB_SUFFIX
from .memory.storage import PersistentKnowledgeStorage
from .navigation.recognizer import UIStateRecognizer, UIStateDetection
from .navigation.graph import NavigationGraph, PathResult
from .navigation.navigator import UINavigator, NavigationResult
from .skills.engine import SkillExecutionEngine, SkillExecutionResult
from .skills.learner import SkillLearner
from .skills.contracts import NavigationNode, NavigationEdge
from .assets.indexer import GameAssetIndexer
from .core.explorer import UIExplorer, UIElementCandidate, PROBE_TIMEOUT_MIN_S, PROBE_TIMEOUT_MAX_S

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

DEFAULT_SKILLS_PACK = str(Path(__file__).resolve().parent.parent / "game_skills" / "hoi4")


class UGMContext:
    """Dependency container holding game MCP subsystems."""

    def __init__(
        self,
        offline_fixture: Optional[str] = None,
        ocr_backend: str = "rapidocr",
        skills_pack_dir: Optional[str] = DEFAULT_SKILLS_PACK,
        db_path: Optional[str] = None,
        fixture_title: Optional[str] = None,
        fixture_process_name: Optional[str] = None,
        enforce_dry_run: bool = False
    ):
        self.window_manager = WindowManager()
        self.capture_manager = CaptureManager(
            backend_override=FixtureBackend() if offline_fixture is not None else None,
            allow_fixture_fallback=False,
            require_window=True,
        )
        backend_key = ocr_backend.strip().lower()
        ocr_options = {}
        if backend_key in {"rapidocr", "rapidocr_lite", "rapidocrlite", "lite"}:
            ocr_options["min_score"] = 0.45
        self.ocr_engine: BaseOCREngine = get_ocr_engine(backend=ocr_backend, **ocr_options)
        self.icon_detector = IconDetector(default_threshold=0.80)
        self.simulation_session = (
            offline_fixture is not None or enforce_dry_run or safety_manager.is_dry_run_enforced
        )
        self.input_controller = InputController(self.window_manager, enforce_dry_run=self.simulation_session)
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
        self._knowledge_db_path = Path(db_path or "data/memory/ugm_memory.db").resolve()
        self.storage = self._open_knowledge_storage(simulation=self.simulation_session)
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
        self.skill_learner = SkillLearner(
            storage=self.storage,
            execution_engine=self.skill_engine
        )

        # V1.5 Asset Indexer
        self.asset_indexer = GameAssetIndexer()

        # V2 UI Explorer & Probing Subsystem
        self.explorer = UIExplorer(
            capture_manager=self.capture_manager,
            input_controller=self.input_controller,
            verifier=self.verifier,
            ocr_engine=self.ocr_engine,
            icon_detector=self.icon_detector,
            storage=self.storage
        )
        self.active_game_id: str = "hoi4"
        self.explorer.active_game_id = self.active_game_id

        if offline_fixture is not None:
            self.setup_offline_fixture(offline_fixture, title=fixture_title, process_name=fixture_process_name)

    def discover_and_attach(
        self,
        window_title: Optional[str] = None,
        process_name: Optional[str] = None,
        auto_detect_foreground: bool = True
    ) -> Dict[str, Any]:
        """
        Discovers and attaches to a game window.
        If the game is new/unknown, registers it in persistent memory with an initially empty
        knowledge state and provisions an empty navigation graph.
        """
        safety_manager.check_safety()
        win = None
        if window_title or process_name:
            win = self.window_manager.find_window(window_title=window_title, process_name=process_name)
        elif auto_detect_foreground:
            win = self.window_manager.get_foreground_window()

        if win is None:
            return {
                "attached": False,
                "status": "not_found",
                "message": f"Could not find or attach to game window (title='{window_title}', process='{process_name}').",
                "game_id": None,
                "window": None
            }

        game_id = game_id_for_window(win)

        self.active_game_id = game_id
        self.window_manager.attach_window(win)
        self.explorer.active_game_id = game_id

        # Register game in persistent storage if not already present
        existing = self.storage.get_game(game_id)
        is_new = False
        if not existing:
            self.storage.register_game(
                game_id=game_id,
                name=win.title or game_id,
                version="1.0.0",
                base_res=(win.width or 1920, win.height or 1080)
            )
            is_new = True

        self.get_navigation_graph(game_id)

        stats = self.storage.get_statistics(game_id=game_id)

        return {
            "attached": True,
            "status": "attached",
            "is_new_game": is_new,
            "game_id": game_id,
            "name": win.title,
            "process_name": win.process_name,
            "window": win.to_dict(),
            "learned_skills_count": stats["total_learned_skills"],
            "learned_states_count": stats["ui_states_count"],
            "knowledge_status": {
                "ui_states": stats["ui_states_count"],
                "transitions": stats["transitions_count"],
                "learned_skills": stats["total_learned_skills"]
            },
            "message": f"Attached to game '{game_id}' ({'newly initialized with empty GameSkill' if is_new else 'resumed existing session'})."
        }

    def _open_knowledge_storage(self, *, simulation: bool) -> PersistentKnowledgeStorage:
        path = str(self._knowledge_db_path)
        if simulation:
            if not path.casefold().endswith(SIMULATION_DB_SUFFIX):
                path += SIMULATION_DB_SUFFIX
        return PersistentKnowledgeStorage(DatabaseManager(path, simulation=simulation))

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
        nodes = ["main_map", "production", "construction", "research", "diplomacy", "recruitment"]
        for nid in nodes:
            graph.register_node(NavigationNode(node_id=nid, menu_name=nid.capitalize(), game_id="hoi4", identification_selector=""))

        # Add bidirectional transitions between main_map and sub-panels
        transitions = [
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
        ]
        for src, tgt, act, params in transitions:
            graph.register_edge(NavigationEdge(source_node_id=src, target_node_id=tgt, trigger_action=act, parameters=params, cost=1.0), persist=False)

    def _populate_sim_navigation_graph(self, graph: NavigationGraph) -> None:
        graph.register_node(NavigationNode(node_id="main_view", menu_name="Main View", game_id="offline_sim", identification_selector=""))
        graph.register_node(NavigationNode(node_id="paused", menu_name="Paused", game_id="offline_sim", identification_selector=""))
        graph.register_edge(NavigationEdge("main_view", "paused", "press_keys", {"keys": ["space"]}, cost=1.0), persist=False)
        graph.register_edge(NavigationEdge("paused", "main_view", "press_keys", {"keys": ["space"]}, cost=1.0), persist=False)

    def setup_offline_fixture(
        self,
        fixture_path: str,
        title: Optional[str] = None,
        process_name: Optional[str] = None
    ) -> None:
        """Sets up a mock game window and fixture image for offline operation."""
        img = cv2.imread(fixture_path)
        if img is None:
            raise ValueError(f"Could not load fixture from {fixture_path}")
        h, w = img.shape[:2]
        if not self.simulation_session:
            # This method can also be called on an existing context. Switch all
            # knowledge owners, not just the capture/input path.
            simulation_storage = self._open_knowledge_storage(simulation=True)
            self.storage.db.close()
            self.storage = simulation_storage
            self.state_recognizer.storage = simulation_storage
            self.skill_engine.storage = simulation_storage
            self.skill_learner.storage = simulation_storage
            self.explorer.storage = simulation_storage
            self.navigation_graphs.clear()
            self.navigators.clear()
            self.pre_capture_cache.clear()
            self.simulation_session = True
            self._init_default_game_metadata()
        mock_win = WindowInfo(
            hwnd=9999,
            title=title or "Hearts of Iron IV (Mock Fixture)",
            process_name=process_name or "hoi4.exe",
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
        self.input_controller.enforce_dry_run = True
        logger.info("UGM configured in offline fixture mode using %s (%dx%d)", fixture_path, w, h)


def create_server(context: Optional[UGMContext] = None) -> MCPServer:
    """Creates and configures the MCP server with the UGM tools."""
    ctx = context or UGMContext()
    server = MCPServer(
        name="universal-game-mcp",
        version=__version__,
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
        full_frame = ctx.capture_manager.capture(window=ctx.window_manager.current_window)
        frame = full_frame
        if norm_roi is not None:
            box = norm_roi.to_pixel(full_frame.shape[1], full_frame.shape[0])
            frame = full_frame[box.y:box.bottom, box.x:box.right]

        h, w = frame.shape[:2]
        saved_file = None
        if save_path:
            saved_file = ctx.capture_manager.save_image(frame, save_path)

        preview_b64 = ctx.capture_manager.encode_base64_png(frame)
        cap_id = f"cap_{uuid4().hex}"
        ctx.pre_capture_cache[cap_id] = full_frame

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
        When enabled, verification checks live visual state change. Dry-run has no verified outcome.
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
        Without a baseline, checks that expected_text appears in the current frame or ROI.
        With pre_capture_id, checks frame change and any requested text in full-window coordinates.
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
            if expected_text is not None:
                min_diff_ratio = 0.0

        ver_res = ctx.verifier.verify_selection(
            pre_frame=pre_frame,
            post_frame=post_frame,
            roi=norm_roi,
            expected_text=expected_text,
            min_diff_ratio=min_diff_ratio
        )
        result = ver_res.to_dict()
        result["evidence_level"] = (
            "simulated" if ctx.simulation_session else
            "verified" if ver_res.verified and (expected_text or min_diff_ratio > 0.0) else "observed"
        )
        return result

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
        game_id: Optional[str] = None,
        hint_state: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Classifies and reports the current game interface state (e.g. main_map, production, research, paused).
        Returns confidence, supporting observable evidence, perceptual hash, and whether state is known/ambiguous/unknown.
        """
        target_game_id = game_id or ctx.active_game_id or "hoi4"
        frame = ctx.capture_manager.capture(window=ctx.window_manager.current_window)
        det = ctx.state_recognizer.recognize_state(
            frame, game_id=target_game_id, hint_state=hint_state,
            simulation=ctx.simulation_session,
            record_observation=not ctx.input_controller.is_dry_run_active() or ctx.simulation_session,
        )
        return det.to_dict()

    # 13. memory_search
    @server.tool()
    def memory_search(
        game_id: Optional[str] = None,
        query: Optional[str] = None,
        search_type: str = "all",
        limit: int = 20
    ) -> Dict[str, Any]:
        """
        Retrieves relevant remembered experience from the persistent SQLite knowledge store.
        Can search 'states', 'transitions', 'skills', 'actions', or 'all'.
        """
        target_game_id = game_id or ctx.active_game_id or "hoi4"
        results: Dict[str, Any] = {"game_id": target_game_id, "search_type": search_type}
        if search_type in ("all", "states"):
            states = ctx.storage.get_states(target_game_id)
            if query:
                states = [s for s in states if query.lower() in s.state_name.lower()]
            results["states"] = [asdict(s) for s in states[:limit]]
        if search_type in ("all", "transitions"):
            transitions = ctx.storage.get_transitions(target_game_id)
            if query:
                transitions = [t for t in transitions if query.lower() in t.from_state.lower() or query.lower() in t.to_state.lower()]
            results["transitions"] = [asdict(t) for t in transitions[:limit]]
        if search_type in ("all", "skills"):
            skills = ctx.storage.list_learned_skills(game_id=target_game_id)
            if query:
                skills = [s for s in skills if query.lower() in s.skill_id.lower() or query.lower() in s.name.lower()]
            results["learned_skills"] = [asdict(s) for s in skills[:limit]]
        if search_type in ("all", "actions"):
            actions = ctx.storage.get_action_history(game_id=target_game_id, limit=limit)
            results["action_history"] = actions
        return results

    # 14. get_navigation_plan
    @server.tool()
    def get_navigation_plan(
        target_state: str,
        start_state: Optional[str] = None,
        game_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Calculates optimal multi-step navigation route to destination UI state using Dijkstra pathfinding.
        If start_state is omitted, live UI recognition identifies current starting position.
        """
        target_game_id = game_id or ctx.active_game_id or "hoi4"
        if not start_state:
            frame = ctx.capture_manager.capture(window=ctx.window_manager.current_window)
            det = ctx.state_recognizer.recognize_state(
                frame, game_id=target_game_id, simulation=ctx.simulation_session,
                record_observation=not ctx.input_controller.is_dry_run_active() or ctx.simulation_session,
            )
            start_state = det.state_name

        graph = ctx.get_navigation_graph(target_game_id)
        plan = graph.plan_route(start_state=start_state, goal_state=target_state)
        return {
            "found": plan.found,
            "start_state": plan.start_state,
            "target_state": plan.target_state,
            "total_cost": plan.total_cost if plan.found else None,
            "steps_count": len(plan.edges),
            "nodes_path": plan.nodes,
            "steps": [asdict(e) for e in plan.edges],
            "message": plan.message
        }

    # 15. navigate_to
    @server.tool()
    def navigate_to(
        target_state: str,
        game_id: Optional[str] = None,
        max_retries: int = 2,
        dry_run: bool = False
    ) -> Dict[str, Any]:
        """
        Safely navigates the game interface from current state to target destination state.
        Verifies resulting state after every single step and replans or halts safely on failure.
        """
        target_game_id = game_id or ctx.active_game_id or "hoi4"
        navigator = ctx.get_navigator(target_game_id)
        res = navigator.navigate_to(
            target_state=target_state,
            game_id=target_game_id,
            max_retries=max_retries,
            dry_run=dry_run
        )
        return res.to_dict()

    # 16. execute_skill
    @server.tool()
    def execute_skill(
        skill_id: str,
        game_id: Optional[str] = None,
        params: Optional[Dict[str, Any]] = None,
        dry_run: bool = False
    ) -> Dict[str, Any]:
        """
        Executes a registered curated or verified learned Game Skill.
        Validates preconditions, dispatches sequential allowlisted primitives, verifies postconditions.
        Dry-run success only means the steps were accepted, not executed or verified.
        An actual postcondition match is indicated by post_verification_passed.
        """
        target_game_id = game_id or ctx.active_game_id or "hoi4"
        res = ctx.skill_engine.execute_skill(
            skill_id=skill_id,
            game_id=target_game_id,
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
        game_id: Optional[str] = None,
        description: str = "",
        preconditions: Optional[Dict[str, Any]] = None,
        post_verification: Optional[Dict[str, Any]] = None,
        source: str = "live",
        initial_confidence: Optional[float] = None,
        tags: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Saves a newly discovered action sequence as a candidate reusable Game Skill in persistent memory.
        Enforces allowlisted primitives, semantic selectors, and protects curated Game Skill Packs.
        """
        target_game_id = game_id or ctx.active_game_id or "hoi4"
        try:
            res = ctx.skill_learner.learn_candidate_skill(
                game_id=target_game_id,
                skill_id=skill_id,
                name=name,
                steps=steps,
                description=description,
                preconditions=preconditions,
                post_verification=post_verification,
                source=source
            )
            if initial_confidence is not None:
                ctx.storage.update_knowledge_state(
                    game_id=target_game_id,
                    item_type="skill",
                    item_id=skill_id,
                    new_status="candidate"
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
        target_game_id = game_id or ctx.active_game_id or "hoi4"
        return ctx.skill_learner.get_learning_status(game_id=target_game_id)

    # 20. delete_learned_memory
    @server.tool()
    def delete_learned_memory(
        game_id: Optional[str] = None,
        skill_id: Optional[str] = None,
        scope: str = "all"
    ) -> Dict[str, Any]:
        """
        Safely deletes learned experience from persistent storage.
        Pass game_id when deleting an individual skill_id shared by multiple games.
        Otherwise delete by game_id and scope ('all', 'history', 'skills', 'transitions', 'states').
        """
        if skill_id:
            deleted = ctx.skill_learner.delete_skill(skill_id, game_id=game_id)
            not_found = "not found" if game_id is not None else "not found or ambiguous; pass game_id if shared"
            return {
                "deleted": deleted,
                "skill_id": skill_id,
                "message": f"Skill '{skill_id}' deleted." if deleted else f"Skill '{skill_id}' {not_found}."
            }
        else:
            target_game_id = game_id or ctx.active_game_id
            return ctx.storage.delete_memory(game_id=target_game_id, scope=scope)

    # 21. index_game_assets
    @server.tool()
    def index_game_assets(
        game_path: str,
        game_id: str = "hoi4"
    ) -> Dict[str, Any]:
        """
        Extracts read-only UI metadata, sprite definitions, and localization keys from installed game files into local index cache.
        Read-only; never alters game files or memory.
        """
        return ctx.asset_indexer.index_game_directory(game_id=game_id, game_path=game_path)

    # =========================================================================
    # V2 Autonomous Exploration, Probing & Knowledge Lifecycle Tools (22-31)
    # =========================================================================

    # 22. discover_and_attach_game
    @server.tool()
    def discover_and_attach_game(
        window_title: Optional[str] = None,
        process_name: Optional[str] = None,
        auto_detect_foreground: bool = True
    ) -> Dict[str, Any]:
        """
        Discovers and attaches to an active game window.
        Initializes initially empty per-game knowledge if previously unseen.
        """
        return ctx.discover_and_attach(
            window_title=window_title,
            process_name=process_name,
            auto_detect_foreground=auto_detect_foreground
        )

    # 23. explore_interactive_elements
    @server.tool()
    def explore_interactive_elements(
        roi: Optional[List[float]] = None,
        scan_mode: str = "all"
    ) -> Dict[str, Any]:
        """
        Scans current game screen (or ROI) for candidate interactive UI affordances:
        buttons, text anchors, tabs, and layout regions.
        """
        if roi is not None and len(roi) != 4:
            raise ValueError("Exploration ROI must contain [x, y, width, height].")
        norm_roi = NormalizedRect(*roi) if roi is not None else None
        frame = ctx.capture_manager.capture(window=ctx.window_manager.current_window)
        candidates = ctx.explorer.discover_interactive_elements(frame, roi=norm_roi, scan_mode=scan_mode)
        return {
            "count": len(candidates),
            "candidates": [c.to_dict() for c in candidates],
            "scan_mode": scan_mode
        }

    # 24. probe_action
    @server.tool()
    def probe_action(
        action: str,
        params: Optional[Dict[str, Any]] = None,
        expected_roi: Optional[List[float]] = None,
        dry_run: bool = False,
        revert_on_fail: bool = False,
        game_id: Optional[str] = None,
        timeout_s: Annotated[float, Field(ge=PROBE_TIMEOUT_MIN_S, le=PROBE_TIMEOUT_MAX_S)] = 5.0,
    ) -> Dict[str, Any]:
        """
        Executes a bounded Observe-Act-Verify probe step.
        Live probes sample for stable change within timeout_s, not proof of a gameplay rule.
        The timeout is cooperative: native capture/input/OCR already in progress may overrun.
        Click and hover require explicit normalized x and y coordinates.
        Dry-run validates the requested action without dispatch or empirical verification.
        revert_on_fail is retained for compatibility; no generic undo is dispatched.
        """
        if expected_roi is not None and len(expected_roi) != 4:
            raise ValueError("expected_roi must contain [x, y, width, height].")
        norm_roi = NormalizedRect(*expected_roi) if expected_roi is not None else None
        return ctx.explorer.probe_step(
            action=action,
            params=params,
            expected_roi=norm_roi,
            timeout_s=timeout_s,
            dry_run=dry_run,
            revert_on_fail=revert_on_fail,
            game_id=game_id
        )

    # 25. manage_knowledge_state
    @server.tool()
    def manage_knowledge_state(
        item_type: str,
        item_id: str,
        status: str,
        game_id: Optional[str] = None,
        reason: str = ""
    ) -> Dict[str, Any]:
        """
        Updates knowledge lifecycle state (candidate, verified, suspect, rejected)
        for a learned skill, transition, or selector.
        """
        target_game_id = game_id or ctx.active_game_id or "hoi4"
        updated = ctx.storage.update_knowledge_state(
            game_id=target_game_id,
            item_type=item_type,
            item_id=item_id,
            new_status=status,
            reason=reason
        )
        return {
            "success": updated,
            "game_id": target_game_id,
            "item_type": item_type,
            "item_id": item_id,
            "status": status,
            "reason": reason
        }

    # 26. create_knowledge_checkpoint
    @server.tool()
    def create_knowledge_checkpoint(
        label: str,
        game_id: Optional[str] = None,
        description: str = ""
    ) -> Dict[str, Any]:
        """
        Creates an atomic point-in-time snapshot checkpoint of all learned knowledge for a game.
        Protects against accidental memory corruption or false inferences during exploration.
        """
        target_game_id = game_id or ctx.active_game_id or "hoi4"
        checkpoint_id = ctx.storage.create_checkpoint(game_id=target_game_id, label=label, description=description)
        return {
            "success": True,
            "checkpoint_id": checkpoint_id,
            "game_id": target_game_id,
            "label": label
        }

    # 27. rollback_knowledge_checkpoint
    @server.tool()
    def rollback_knowledge_checkpoint(
        checkpoint_id: int,
        game_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Atomically restores game knowledge to a previously saved checkpoint.
        """
        target_game_id = game_id or ctx.active_game_id or "hoi4"
        success = ctx.storage.rollback_checkpoint(game_id=target_game_id, checkpoint_id=checkpoint_id)
        if success:
            # The navigator holds a reference to its graph. Recreate both lazily
            # through the normal loader, which also restores built-in routes.
            ctx.navigators.pop(target_game_id, None)
            ctx.navigation_graphs.pop(target_game_id, None)
        return {
            "success": success,
            "game_id": target_game_id,
            "checkpoint_id": checkpoint_id,
            "message": f"Rollback to checkpoint {checkpoint_id} {'succeeded' if success else 'failed'}."
        }

    # 28. list_knowledge_checkpoints
    @server.tool()
    def list_knowledge_checkpoints(
        game_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Lists all saved knowledge checkpoints for a game.
        """
        target_game_id = game_id or ctx.active_game_id or "hoi4"
        checkpoints = ctx.storage.list_checkpoints(game_id=target_game_id)
        return {
            "game_id": target_game_id,
            "count": len(checkpoints),
            "checkpoints": checkpoints
        }

    # 29. query_cross_game_concepts
    @server.tool()
    def query_cross_game_concepts(
        category: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Queries universal cross-game heuristics and concepts (e.g. pause_toggle, cancel_dialog, speed controls).
        Provides initial exploration priors without hardcoded game logic.
        """
        concepts = ctx.storage.get_cross_game_concepts(category=category)
        return {
            "count": len(concepts),
            "concepts": concepts
        }

    # 29b. register_cross_game_concept
    @server.tool()
    def register_cross_game_concept(
        concept_id: str,
        category: str,
        name: str,
        description: str,
        default_action: str,
        default_params: Dict[str, Any],
        typical_effects: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Registers or updates a reusable cross-game concept heuristic in persistent memory.
        """
        success = ctx.storage.register_cross_game_concept(
            concept_id=concept_id,
            category=category,
            name=name,
            description=description,
            default_action=default_action,
            default_params=default_params,
            typical_effects=typical_effects
        )
        return {
            "success": success,
            "concept_id": concept_id,
            "message": f"Cross-game concept '{concept_id}' registered successfully."
        }

    # 30. optimize_skill
    @server.tool()
    def optimize_skill(
        skill_id: str,
        steps: List[Dict[str, Any]],
        rationale: str = "",
        game_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Optimizes a learned Game Skill by refining its step sequence, logging rationale,
        and bumping its version number while preserving execution track records.
        Pass game_id if the skill_id is shared by multiple games.
        """
        try:
            success = ctx.skill_learner.optimize_skill(
                skill_id=skill_id, new_steps=steps, rationale=rationale, game_id=game_id
            )
            not_found = "not found" if game_id is not None else "not found or ambiguous; pass game_id if shared"
            return {
                "success": success,
                "skill_id": skill_id,
                "message": f"Skill '{skill_id}' {'optimized successfully' if success else not_found}."
            }
        except (SafetyViolationError, ValueError) as e:
            return {
                "success": False,
                "skill_id": skill_id,
                "message": f"Optimization rejected: {e}"
            }

    # 31. prune_temporary_data
    @server.tool()
    def prune_temporary_data(
        game_id: Optional[str] = None,
        max_age_seconds: int = 3600,
        max_history_records: int = 1000,
        screenshot_dir: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Cleans up temporary screenshot files and prunes redundant action history entries.
        """
        pruned_screenshots = ctx.storage.prune_temporary_screenshots(directory=screenshot_dir, max_age_seconds=max_age_seconds)
        pruned_history = ctx.storage.prune_history(game_id=game_id, max_records=max_history_records)
        return {
            "pruned_screenshots": pruned_screenshots,
            "pruned_history_records": pruned_history,
            "game_id": game_id or "all_games"
        }

    return server


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Universal Game MCP Server (UGM)")
    parser.add_argument("--fixture", type=str, default=None, help="Path to offline fixture image for testing")
    parser.add_argument("--dry-run", action="store_true", help="Enforce dry-run mode for all input actions")
    parser.add_argument("--db-path", type=Path, help="SQLite knowledge database path (simulation sessions use a separate .simulation file)")
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args()
    if args.fixture is not None and not Path(args.fixture).is_file():
        parser.error("the fixture must be an existing image file")

    ctx = UGMContext(
        offline_fixture=args.fixture, db_path=args.db_path, enforce_dry_run=args.dry_run,
    )

    server = create_server(ctx)
    logger.info("Starting Universal Game MCP (UGM) server via stdio transport...")
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
