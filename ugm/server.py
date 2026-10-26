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
    HoverResult
)
from .core.safety import safety_manager, EmergencyStopTriggered, SafetyViolationError
from .core.window import WindowManager
from .core.capture import CaptureManager, FixtureBackend
from .core.ocr import RapidOCREngine, BaseOCREngine
from .core.icon import IconDetector
from .core.input import InputController
from .core.verifier import ActionVerifier
from .core.dataset import DatasetCollector
from .adapters.hoi4.adapter import HOI4Adapter

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

    def __init__(self, offline_fixture: Optional[str] = None):
        self.window_manager = WindowManager()
        self.capture_manager = CaptureManager()
        self.ocr_engine: BaseOCREngine = RapidOCREngine(min_score=0.45, use_cls=False)
        self.icon_detector = IconDetector(default_threshold=0.80)
        self.input_controller = InputController(self.window_manager, dry_run_default=False)
        self.verifier = ActionVerifier(self.capture_manager, self.ocr_engine, self.input_controller)
        self.dataset_collector = DatasetCollector()
        self.pre_capture_cache: Dict[str, np.ndarray] = {}

        # Register HOI4 Adapter
        self.hoi4_adapter = HOI4Adapter()
        self.hoi4_adapter.register_icons(self.icon_detector)

        if offline_fixture and os.path.exists(offline_fixture):
            self.setup_offline_fixture(offline_fixture)

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
        version="1.0.0",
        instructions="Universal Game MCP (UGM) allows AI to observe, read text, locate icons, and interact safely with offline games."
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

    # 4. locate_icon
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
        norm_roi = NormalizedRect(roi[0], roi[1], roi[2], roi[3]) if roi and len(roi) == 4 else None
        frame = ctx.capture_manager.capture(window=ctx.window_manager.current_window)

        custom_img = None
        if template_base64:
            raw_bytes = base64.b64decode(template_base64)
            nparr = np.frombuffer(raw_bytes, np.uint8)
            custom_img = cv2.imdecode(nparr, cv2.IMREAD_UNCHANGED)

        res = ctx.icon_detector.locate_icon(
            image=frame,
            template_name=icon_name,
            template_image=custom_img,
            roi=norm_roi,
            threshold=threshold
        )
        return res.to_dict()

    # 5. hover_target
    @server.tool()
    def hover_target(
        x: float,
        y: float,
        duration_ms: int = 500,
        read_tooltip: bool = True,
        dry_run: bool = False
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
        x: float,
        y: float,
        button: str = "left",
        clicks: int = 1,
        dry_run: bool = False,
        verify: bool = True
    ) -> Dict[str, Any]:
        """
        Safely clicks at normalized coordinates (x, y) within the game window via DirectInput.
        Supports dry-run and post-click visual verification.
        """
        pre_frame = None
        if verify:
            pre_frame = ctx.capture_manager.capture(window=ctx.window_manager.current_window)

        click_res = ctx.input_controller.click(
            norm_x=x,
            norm_y=y,
            button=button,
            clicks=clicks,
            dry_run=dry_run
        )

        verification_passed = None
        if verify and click_res.executed and pre_frame is not None:
            time.sleep(0.08)
            post_frame = ctx.capture_manager.capture(window=ctx.window_manager.current_window)
            ver_res = ctx.verifier.verify_selection(pre_frame, post_frame)
            verification_passed = ver_res.verified
            click_res.verification_passed = verification_passed

        res_dict = click_res.to_dict()
        return res_dict

    # 7. press_keys
    @server.tool()
    def press_keys(
        keys: List[str],
        duration_ms: int = 100,
        dry_run: bool = False
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

    # 8. verify_selection
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
        norm_roi = NormalizedRect(roi[0], roi[1], roi[2], roi[3]) if roi and len(roi) == 4 else None
        post_frame = ctx.capture_manager.capture(window=ctx.window_manager.current_window)

        pre_frame = None
        if pre_capture_id and pre_capture_id in ctx.pre_capture_cache:
            pre_frame = ctx.pre_capture_cache[pre_capture_id]
        else:
            # If no cached capture, use current frame as baseline
            pre_frame = post_frame

        ver_res = ctx.verifier.verify_selection(
            pre_frame=pre_frame,
            post_frame=post_frame,
            roi=norm_roi,
            expected_text=expected_text
        )
        return ver_res.to_dict()

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

    return server


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Universal Game MCP Server (UGM)")
    parser.add_argument("--fixture", type=str, default=None, help="Path to offline fixture image for testing")
    parser.add_argument("--dry-run", action="store_true", help="Enforce dry-run mode for all input actions")
    args = parser.parse_args()

    ctx = UGMContext(offline_fixture=args.fixture)
    if args.dry_run:
        ctx.input_controller.dry_run_default = True

    server = create_server(ctx)
    logger.info("Starting Universal Game MCP (UGM) server via stdio transport...")
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
