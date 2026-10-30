"""
Universal Game MCP (UGM) - Safe Input Controller
Controls mouse and keyboard inputs via PyDirectInput (Win32 DirectInput scan codes),
strictly bounded within the target game window with dry-run and emergency stop enforcement.
"""

from __future__ import annotations
import sys
import time
import logging
from typing import Optional, List, Tuple, Any, Set

from .types import WindowInfo, ClickResult, ActionResult, ClickButton
from .safety import safety_manager, SafetyViolationError, EmergencyStopTriggered
from .window import WindowManager

logger = logging.getLogger("ugm.input")

try:
    import pydirectinput
    pydirectinput.FAILSAFE = True
    FailSafeException = getattr(
        pydirectinput,
        "FailSafeException",
        type("FailSafeException", (Exception,), {})
    )
except Exception:
    pydirectinput = None

    class FailSafeException(Exception):
        pass


class InputController:
    """
    DirectInput mouse and keyboard controller for offline games.
    Safely translates normalized window coordinates into screen pixels.
    """

    # Game-Safe Key Allowlist (Letters, digits, F-keys, numpad, navigation, modifiers, punctuation)
    SAFE_KEY_ALLOWLIST: Set[str] = {
        # Lowercase letters
        *(chr(c) for c in range(ord('a'), ord('z') + 1)),
        # Digits
        *(str(d) for d in range(10)),
        # Function keys (F1 - F12)
        *(f"f{i}" for i in range(1, 13)),
        # Numpad & Math operators
        "num+", "num-", "num*", "num/", "numenter", "numpad.",
        *(f"num{i}" for i in range(10)),
        *(f"numpad{i}" for i in range(10)),
        "+", "-", "*", "/", "=",
        # Navigation & Editing
        "space", "enter", "return", "escape", "esc", "tab", "backspace", "delete", "del",
        "up", "down", "left", "right", "pageup", "pagedown", "home", "end", "insert",
        "pause",
        # Modifiers
        "ctrl", "ctrlleft", "ctrlright", "shift", "shiftleft", "shiftright", "alt", "altleft", "altright",
        # Punctuation & symbols
        ",", ".", ";", "'", "[", "]", "`", "\\", "~"
    }

    # Forbidden dangerous system keys (instant rejection)
    FORBIDDEN_KEYS: Set[str] = {
        "win", "windows", "winleft", "winright",
        "super", "cmd", "command", "meta",
        "printscreen", "prtsc", "sysrq", "sleep", "power",
        "apps", "menu", "scrolllock"
    }

    # Forbidden dangerous combinations (instant rejection)
    FORBIDDEN_COMBINATIONS: List[Set[str]] = [
        {"alt", "f4"},
        {"ctrl", "alt", "del"},
        {"ctrl", "alt", "delete"},
        {"alt", "tab"},
        {"ctrl", "esc"},
        {"ctrl", "escape"},
        {"alt", "space"},
    ]

    MODIFIER_KEYS: Set[str] = {
        "ctrl", "ctrlleft", "ctrlright",
        "shift", "shiftleft", "shiftright",
        "alt", "altleft", "altright"
    }

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

    def check_corner_abort(self) -> None:
        """
        Fallback corner abort check: if mouse cursor is within 5px of (0,0),
        immediately triggers emergency stop and raises EmergencyStopTriggered.
        """
        try:
            cursor_x, cursor_y = None, None
            if pydirectinput is not None and hasattr(pydirectinput, "position"):
                pos = pydirectinput.position()
                if pos is not None:
                    cursor_x, cursor_y = pos[0], pos[1]
            if cursor_x is None and sys.platform == "win32":
                try:
                    import win32gui
                    cursor_x, cursor_y = win32gui.GetCursorPos()
                except Exception:
                    pass

            if cursor_x is not None and cursor_y is not None:
                if 0 <= cursor_x <= 5 and 0 <= cursor_y <= 5:
                    safety_manager.trigger_emergency_stop(
                        "Hardware corner abort triggered: mouse cursor within 5px of (0,0)."
                    )
                    raise EmergencyStopTriggered(
                        "Hardware failsafe triggered: mouse cursor at screen corner."
                    )
        except EmergencyStopTriggered:
            raise
        except Exception as e:
            logger.debug("Corner abort check note: %s", e)

    def normalize_and_validate_keys(self, keys: List[str]) -> List[List[str]]:
        """
        Parses and validates key combinations and sequential keystrokes against
        SAFE_KEY_ALLOWLIST and FORBIDDEN_KEYS / FORBIDDEN_COMBINATIONS.
        Returns a list of key groups to execute sequentially, where each group
        is a list of keys pressed simultaneously as a combo (e.g. [['shift', 'q']]).
        """
        if not keys:
            raise SafetyViolationError("Keys list cannot be empty.")

        normalized_tokens = [k.strip().lower() for k in keys if isinstance(k, str) and k.strip()]
        if not normalized_tokens:
            raise SafetyViolationError("Keys list contains only empty strings.")

        # 1. Blocklist inspection on all raw tokens and sub-tokens
        for token in normalized_tokens:
            sub_tokens = (
                [t.strip() for t in token.split("+") if t.strip()]
                if ("+" in token and token not in ("+", "num+"))
                else [token]
            )
            for st in sub_tokens:
                if st in self.FORBIDDEN_KEYS:
                    raise SafetyViolationError(
                        f"Forbidden dangerous key '{st}' rejected by security policy."
                    )

        # 2. Form A detection: if the list itself is a modifier combo (e.g. ['shift', 'q'])
        if (
            len(normalized_tokens) > 1
            and all(k in self.MODIFIER_KEYS for k in normalized_tokens[:-1])
            and normalized_tokens[-1] not in self.MODIFIER_KEYS
        ):
            token_set = set(normalized_tokens)
            for forbidden_combo in self.FORBIDDEN_COMBINATIONS:
                if forbidden_combo.issubset(token_set):
                    raise SafetyViolationError(
                        f"Forbidden key combination rejected: {forbidden_combo}"
                    )
            for k in normalized_tokens:
                if k not in self.SAFE_KEY_ALLOWLIST:
                    raise SafetyViolationError(
                        f"Key '{k}' is not in safe key allowlist."
                    )
            return [normalized_tokens]

        # 3. Form B & sequential actions
        actions: List[List[str]] = []
        for entry in normalized_tokens:
            if "+" in entry and entry not in ("+", "num+"):
                if entry.endswith("++"):
                    parts = [p.strip() for p in entry[:-1].split("+") if p.strip()] + ["+"]
                else:
                    parts = [p.strip() for p in entry.split("+") if p.strip()]
            else:
                parts = [entry]

            token_set = set(parts)
            for forbidden_combo in self.FORBIDDEN_COMBINATIONS:
                if forbidden_combo.issubset(token_set):
                    raise SafetyViolationError(
                        f"Forbidden key combination rejected: {forbidden_combo}"
                    )

            for k in parts:
                if k in self.FORBIDDEN_KEYS:
                    raise SafetyViolationError(
                        f"Forbidden dangerous key '{k}' rejected by security policy."
                    )
                if k not in self.SAFE_KEY_ALLOWLIST:
                    raise SafetyViolationError(
                        f"Key '{k}' is not in safe key allowlist."
                    )

            actions.append(parts)

        return actions

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

        try:
            self.check_corner_abort()
            if pydirectinput is not None:
                pydirectinput.moveTo(screen_x, screen_y)
            time.sleep(max(0.01, duration_ms / 1000.0))
            self.check_corner_abort()
            return screen_x, screen_y, True
        except FailSafeException as e:
            safety_manager.trigger_emergency_stop(f"Hardware failsafe corner abort triggered: {e}")
            raise EmergencyStopTriggered(f"Hardware failsafe triggered: {e}") from e

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

        try:
            self.check_corner_abort()
            if pydirectinput is not None:
                pydirectinput.moveTo(screen_x, screen_y)
            time.sleep(0.05)

            for _ in range(clicks):
                self.check_corner_abort()
                if pydirectinput is not None:
                    if button == "right":
                        pydirectinput.rightClick(screen_x, screen_y)
                    elif button == "middle":
                        pydirectinput.middleClick(screen_x, screen_y)
                    else:
                        pydirectinput.click(screen_x, screen_y)
                time.sleep(0.05)
        except FailSafeException as e:
            safety_manager.trigger_emergency_stop(f"Hardware failsafe corner abort triggered: {e}")
            raise EmergencyStopTriggered(f"Hardware failsafe triggered: {e}") from e

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
        Validates keys against allowlist, verifies window focus, checks emergency stop on each step.
        """
        t0 = time.perf_counter()
        # 1. Parse and validate keys (raises SafetyViolationError if invalid)
        action_groups = self.normalize_and_validate_keys(keys)

        # 2. Verify target window existence
        win = self.window_manager.current_window
        if win is None:
            raise SafetyViolationError("No target game window selected. Cannot press keys.")

        # 3. Anti-cheat check
        if win.process_name:
            safety_manager.validate_process(win.process_name)

        # 4. Check emergency stop
        safety_manager.check_safety()

        # 5. Check corner abort
        self.check_corner_abort()

        is_dry_run = self.is_dry_run_active(dry_run)
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

        # Live path: verify window focus
        if self.auto_focus and not win.is_focused:
            self.window_manager.focus_window(win.hwnd)
            win = self.window_manager.current_window or win

        if not win.is_focused:
            raise SafetyViolationError("Target window lost focus before input execution")

        hold_time = max(0.02, duration_ms / 1000.0)

        for group in action_groups:
            self.check_corner_abort()
            safety_manager.check_safety()

            modifiers = [k for k in group[:-1] if k in self.MODIFIER_KEYS]
            primary = group[-1]
            if group[0] in self.MODIFIER_KEYS and len(group) == 1:
                modifiers = []
                primary = group[0]

            held_keys: List[str] = []
            try:
                # Hold modifier keys
                for mod in modifiers:
                    self.check_corner_abort()
                    safety_manager.check_safety()
                    if pydirectinput is not None:
                        pydirectinput.keyDown(mod)
                    held_keys.append(mod)
                    time.sleep(0.01)

                # Press primary key
                self.check_corner_abort()
                safety_manager.check_safety()
                if pydirectinput is not None:
                    pydirectinput.keyDown(primary)
                held_keys.append(primary)
                time.sleep(hold_time)
                if pydirectinput is not None:
                    pydirectinput.keyUp(primary)
                held_keys.remove(primary)
                time.sleep(0.02)
            except FailSafeException as e:
                safety_manager.trigger_emergency_stop(f"Hardware failsafe corner abort triggered: {e}")
                raise EmergencyStopTriggered(f"Hardware failsafe triggered: {e}") from e
            except (EmergencyStopTriggered, SafetyViolationError):
                raise
            except Exception as e:
                logger.error("Error pressing key group %s: %s", group, e)
                return ActionResult(
                    success=False,
                    action="press_keys",
                    message=f"Failed to press keys {group}: {e}",
                    dry_run=False,
                    latency_ms=(time.perf_counter() - t0) * 1000.0,
                    details={"failed_keys": group}
                )
            finally:
                # Guaranteed release of all depressed keys in reverse order
                for key_to_release in reversed(held_keys):
                    try:
                        if pydirectinput is not None:
                            pydirectinput.keyUp(key_to_release)
                    except Exception as e:
                        logger.warning("Error releasing held key '%s': %s", key_to_release, e)

        return ActionResult(
            success=True,
            action="press_keys",
            message=f"Successfully dispatched keystrokes: {keys}",
            dry_run=False,
            latency_ms=(time.perf_counter() - t0) * 1000.0,
            details={"keys": keys, "count": len(keys)}
        )
