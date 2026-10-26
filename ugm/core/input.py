"""
Universal Game MCP (UGM) - Safe Input Controller
Controls mouse and keyboard inputs via PyDirectInput (Win32 DirectInput scan codes),
strictly bounded within the target game window with dry-run and emergency stop enforcement.
"""

from __future__ import annotations
import sys
import time
import logging
from typing import Optional, List, Tuple, Any

from .types import WindowInfo, ClickResult, ActionResult, ClickButton
from .safety import safety_manager, SafetyViolationError
from .window import WindowManager

logger = logging.getLogger("ugm.input")

try:
    import pydirectinput
    # UGM enforces its own window bounds checking and safety manager emergency stop
    pydirectinput.FAILSAFE = False
except Exception:
    pass


class InputController:
    """
    DirectInput mouse and keyboard controller for offline games.
    Safely translates normalized window coordinates into screen pixels.
    """

    def __init__(
        self,
        window_manager: WindowManager,
        dry_run_default: bool = False,
        auto_focus: bool = True,
        enforce_dry_run: bool = False
    ):
        self.window_manager = window_manager
        self.dry_run_default = dry_run_default
        self.auto_focus = auto_focus
        self.enforce_dry_run = enforce_dry_run

    def is_dry_run_active(self, dry_run: Optional[bool] = None) -> bool:
        """
        Calculates effective dry-run state.
        Server-wide dry-run (enforce_dry_run or safety_manager.is_dry_run_enforced)
        CANNOT be overridden by an individual tool call passing dry_run=False.
        """
        if safety_manager.is_dry_run_enforced or self.enforce_dry_run:
            return True
        if dry_run is not None:
            return bool(dry_run)
        return self.dry_run_default

    def _resolve_screen_coords(
        self,
        norm_x: float,
        norm_y: float,
        dry_run: Optional[bool] = None
    ) -> Tuple[int, int, WindowInfo]:
        """
        Validates normalized coordinates and converts them to absolute screen pixels
        inside the target game window client rectangle.
        """
        # Safety checks
        safety_manager.check_safety()
        safety_manager.validate_normalized_coordinates(norm_x, norm_y)

        win = self.window_manager.current_window
        if win is None:
            raise SafetyViolationError("No target game window selected. Cannot perform input.")

        # Anti-cheat check
        if win.process_name:
            safety_manager.validate_process(win.process_name)

        if win.is_minimized:
            raise SafetyViolationError(f"Target game window '{win.title}' is minimized.")

        # Ensure window is focused if auto_focus is enabled
        effective_dry_run = self.is_dry_run_active(dry_run)
        if self.auto_focus and not win.is_focused:
            focused = self.window_manager.focus_window(win.hwnd)
            if focused:
                # Refresh window info
                win = self.window_manager.current_window or win
            elif not effective_dry_run:
                logger.warning("Could not focus target game window '%s'", win.title)

        # Map normalized [0, 1] to window client area
        screen_x = int(win.client_x + norm_x * win.client_width)
        screen_y = int(win.client_y + norm_y * win.client_height)

        # Strict containment verification
        min_x = win.client_x
        max_x = win.client_x + win.client_width
        min_y = win.client_y
        max_y = win.client_y + win.client_height

        if not (min_x <= screen_x <= max_x and min_y <= screen_y <= max_y):
            raise SafetyViolationError(
                f"Computed screen position ({screen_x}, {screen_y}) falls outside window bounds: "
                f"[{min_x}, {min_y}, {max_x}, {max_y}]"
            )

        return screen_x, screen_y, win

    def hover(
        self,
        norm_x: float,
        norm_y: float,
        duration_ms: int = 500,
        dry_run: Optional[bool] = None
    ) -> Tuple[int, int, bool]:
        """
        Moves the mouse cursor to the target normalized coordinate and hovers for duration_ms.
        Returns (screen_x, screen_y, executed).
        """
        is_dry_run = self.is_dry_run_active(dry_run)
        screen_x, screen_y, win = self._resolve_screen_coords(norm_x, norm_y, dry_run=is_dry_run)

        if is_dry_run:
            logger.info(
                "[DRY-RUN] Hover at norm=(%.4f, %.4f) -> screen=(%d, %d) on '%s' for %dms",
                norm_x, norm_y, screen_x, screen_y, win.title, duration_ms
            )
            time.sleep(min(0.05, duration_ms / 1000.0))
            return screen_x, screen_y, False

        import pydirectinput
        pydirectinput.moveTo(screen_x, screen_y)
        time.sleep(max(0.01, duration_ms / 1000.0))
        return screen_x, screen_y, True

    def click(
        self,
        norm_x: float,
        norm_y: float,
        button: str = "left",
        clicks: int = 1,
        dry_run: Optional[bool] = None
    ) -> ClickResult:
        """
        Executes a safe DirectInput click at normalized coordinates within the game window.
        """
        t0 = time.perf_counter()
        is_dry_run = self.is_dry_run_active(dry_run)

        try:
            screen_x, screen_y, win = self._resolve_screen_coords(norm_x, norm_y, dry_run=is_dry_run)
        except Exception as e:
            return ClickResult(
                x_normalized=norm_x,
                y_normalized=norm_y,
                x_pixel=0,
                y_pixel=0,
                button=button,
                clicks=clicks,
                dry_run=is_dry_run,
                executed=False,
                verification_passed=None,
                reason=f"Safety check rejected click: {e}",
                latency_ms=(time.perf_counter() - t0) * 1000.0
            )

        if is_dry_run:
            logger.info(
                "[DRY-RUN] Click '%s' x%d at norm=(%.4f, %.4f) -> screen=(%d, %d) on '%s'",
                button, clicks, norm_x, norm_y, screen_x, screen_y, win.title
            )
            return ClickResult(
                x_normalized=norm_x,
                y_normalized=norm_y,
                x_pixel=screen_x,
                y_pixel=screen_y,
                button=button,
                clicks=clicks,
                dry_run=True,
                executed=False,
                verification_passed=None,
                reason="Dry-run simulated successfully (no hardware input dispatched).",
                latency_ms=(time.perf_counter() - t0) * 1000.0
            )

        import pydirectinput
        pydirectinput.moveTo(screen_x, screen_y)
        time.sleep(0.05)

        for _ in range(clicks):
            if button == "right":
                pydirectinput.rightClick(screen_x, screen_y)
            elif button == "middle":
                pydirectinput.middleClick(screen_x, screen_y)
            else:
                pydirectinput.click(screen_x, screen_y)
            time.sleep(0.05)

        latency_ms = (time.perf_counter() - t0) * 1000.0
        return ClickResult(
            x_normalized=norm_x,
            y_normalized=norm_y,
            x_pixel=screen_x,
            y_pixel=screen_y,
            button=button,
            clicks=clicks,
            dry_run=False,
            executed=True,
            verification_passed=None,
            reason="Click executed successfully via DirectInput.",
            latency_ms=latency_ms
        )

    def press_keys(
        self,
        keys: List[str],
        duration_ms: int = 100,
        dry_run: Optional[bool] = None
    ) -> ActionResult:
        """
        Sends sequential or combo keystrokes to the target game window.
        """
        t0 = time.perf_counter()
        safety_manager.check_safety()
        is_dry_run = self.is_dry_run_active(dry_run)

        win = self.window_manager.current_window
        if win is None:
            raise SafetyViolationError("No target game window selected. Cannot press keys.")

        if win.process_name:
            safety_manager.validate_process(win.process_name)

        if self.auto_focus and not win.is_focused:
            self.window_manager.focus_window(win.hwnd)

        if is_dry_run:
            logger.info("[DRY-RUN] Press keys %s on '%s'", keys, win.title)
            return ActionResult(
                success=True,
                action="press_keys",
                message=f"[DRY-RUN] Simulated keypresses: {keys}",
                dry_run=True,
                latency_ms=(time.perf_counter() - t0) * 1000.0,
                details={"keys": keys, "duration_ms": duration_ms}
            )

        import pydirectinput
        hold_time = max(0.02, duration_ms / 1000.0)

        for key in keys:
            normalized_key = key.lower().strip()
            # DirectInput key press
            try:
                pydirectinput.keyDown(normalized_key)
                time.sleep(hold_time)
                pydirectinput.keyUp(normalized_key)
                time.sleep(0.02)
            except Exception as e:
                logger.error("Error pressing key '%s': %s", key, e)
                return ActionResult(
                    success=False,
                    action="press_keys",
                    message=f"Failed to press key '{key}': {e}",
                    dry_run=False,
                    latency_ms=(time.perf_counter() - t0) * 1000.0,
                    details={"failed_key": key}
                )

        return ActionResult(
            success=True,
            action="press_keys",
            message=f"Successfully dispatched keystrokes: {keys}",
            dry_run=False,
            latency_ms=(time.perf_counter() - t0) * 1000.0,
            details={"keys": keys, "count": len(keys)}
        )
