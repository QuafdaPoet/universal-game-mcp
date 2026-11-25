"""
Universal Game MCP (UGM) - Window Management
Provides DPI-aware window detection, geometry queries, focus management,
and mocking capabilities for headless unit testing.
"""

from __future__ import annotations
import sys
import logging
from typing import Optional, List, Tuple
from .types import WindowInfo, BoundingBox
from .safety import safety_manager

logger = logging.getLogger("ugm.window")

# Enable per-monitor DPI awareness on Windows
if sys.platform == "win32":
    try:
        import ctypes
        # Per-monitor DPI aware V2
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            import ctypes
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception as e:
            logger.debug("DPI awareness initialization note: %s", e)


def game_id_for_window(window: WindowInfo) -> str:
    """Keep attached-window identity and live evidence in the same game namespace."""
    raw_name = window.process_name or window.title or "unknown_game"
    clean_name = raw_name.lower().replace(".exe", "")
    return "".join(c if c.isalnum() or c == "_" else "_" for c in clean_name).strip("_") or "unknown_game"


def window_binding(window: WindowInfo) -> tuple:
    """Return the identity and geometry used to scope capture and dispatch."""
    return (
        window.hwnd, window.pid, window.process_name.casefold(),
        window.client_x, window.client_y, window.client_width, window.client_height,
        window.dpi_scale,
    )


class WindowManager:
    """Manages game window discovery, rect mapping, and focus control."""

    def __init__(self, mock_window: Optional[WindowInfo] = None):
        self._mock_window = mock_window
        self._current_window: Optional[WindowInfo] = mock_window
        self._attachment_revision = 0

    def set_mock_window(self, mock: Optional[WindowInfo]) -> None:
        """Sets a mock window for testing in headless or virtual environments."""
        self._mock_window = mock
        self._current_window = mock
        self._attachment_revision += 1

    @property
    def attachment_revision(self) -> int:
        return self._attachment_revision

    def attach_window(self, win: Optional[WindowInfo]) -> None:
        """Set the active window; only explicit fixture setup creates a mock."""
        self._current_window = win
        if self._mock_window is not None:
            self._mock_window = win
        self._attachment_revision += 1

    @property
    def current_window(self) -> Optional[WindowInfo]:
        if self._mock_window is not None:
            return self._mock_window
        revision = self._attachment_revision
        previous = self._current_window
        if previous is None or sys.platform != "win32":
            return previous

        fresh = None
        try:
            import win32gui

            if win32gui.IsWindow(previous.hwnd) and win32gui.IsWindowVisible(previous.hwnd):
                candidate = self.get_window_info(previous.hwnd)
                if (
                    candidate.hwnd == previous.hwnd
                    and candidate.pid == previous.pid
                    and candidate.process_name.casefold() == previous.process_name.casefold()
                    and candidate.pid > 0
                    and candidate.process_name.casefold() not in {"", "unknown"}
                ):
                    fresh = candidate
        except Exception as exc:
            logger.debug("Could not refresh attached window: %s", exc)
        if self._current_window is not previous or self._attachment_revision != revision:
            # A concurrent attach changed the selection during refresh.
            return None
        if fresh is None or window_binding(fresh) != window_binding(previous):
            self._attachment_revision += 1
        self._current_window = fresh
        return fresh

    def refresh_current_window(self) -> Optional[WindowInfo]:
        """Explicitly re-queries the window geometry and state from the OS."""
        return self.current_window

    def get_foreground_window(self) -> Optional[WindowInfo]:
        """Resolve the current visible foreground window without using a stale attachment."""
        if self._mock_window is not None:
            return self._mock_window
        if sys.platform != "win32":
            return None

        try:
            import win32gui

            hwnd = win32gui.GetForegroundWindow()
            if not hwnd or not win32gui.IsWindow(hwnd) or not win32gui.IsWindowVisible(hwnd):
                return None
            info = self.get_window_info(hwnd)
        except Exception as exc:
            logger.debug("Could not resolve the foreground window: %s", exc)
            return None

        if info.hwnd != hwnd or not info.title or not info.is_focused or not self._has_live_identity(info):
            return None
        safety_manager.validate_process(info.process_name)
        return info

    @staticmethod
    def _has_live_identity(info: WindowInfo) -> bool:
        return (
            bool(info.hwnd and info.pid > 0 and info.process_name)
            and info.process_name.lower() != "unknown"
            and not info.is_minimized
            and info.client_width > 0
            and info.client_height > 0
        )

    @staticmethod
    def _process_matches(requested: Optional[str], actual: str) -> bool:
        if requested is None:
            return True
        return requested.casefold().removesuffix(".exe") == actual.casefold().removesuffix(".exe")

    def find_window(
        self,
        window_title: Optional[str] = None,
        process_name: Optional[str] = None
    ) -> Optional[WindowInfo]:
        """
        Locate a window by literal title substring and/or exact process name (.exe optional).
        """
        if self._mock_window is not None:
            matches_title = True
            if window_title:
                matches_title = bool(self._mock_window.title and window_title.casefold() in self._mock_window.title.casefold())

            if matches_title and self._process_matches(process_name, self._mock_window.process_name):
                self.attach_window(self._mock_window)
                return self._mock_window
            return None

        if sys.platform != "win32":
            logger.warning("Native window management is only supported on Windows.")
            return None

        import win32gui
        import win32process
        import psutil

        matches: List[Tuple[int, int]] = []

        def enum_windows_callback(hwnd: int, _extra: None) -> bool:
            try:
                if not win32gui.IsWindowVisible(hwnd):
                    return True
                title = win32gui.GetWindowText(hwnd).strip()
                if not title:
                    return True

                try:
                    _, pid = win32process.GetWindowThreadProcessId(hwnd)
                    pname = psutil.Process(pid).name()
                except Exception:
                    pname = ""

                title_matched = not window_title or window_title.casefold() in title.casefold()
                if pname and title_matched and self._process_matches(process_name, pname):
                    matches.append((hwnd, pid))
                return True
            except Exception as e:
                logger.debug("Exception in enum_windows_callback for hwnd %s: %s", hwnd, e)
                return True

        try:
            win32gui.EnumWindows(enum_windows_callback, None)
        except Exception as e:
            logger.debug("EnumWindows call ended or timed out: %s", e)

        if not matches:
            logger.warning("No window found matching title='%s', process='%s'", window_title, process_name)
            return None

        for hwnd, pid in matches:
            # A matching window may close or its handle may be reused.
            try:
                info = self.get_window_info(hwnd)
            except Exception as exc:
                logger.debug("Could not resolve window %s: %s", hwnd, exc)
                continue
            if (
                info.hwnd != hwnd or info.pid != pid
                or not info.title or not self._has_live_identity(info)
                or (window_title and window_title.casefold() not in info.title.casefold())
                or not self._process_matches(process_name, info.process_name)
            ):
                continue
            # Do not absorb anti-cheat denials into an ordinary discovery miss.
            safety_manager.validate_process(info.process_name)
            self.attach_window(info)
            return info
        return None

    def get_window_info(
        self,
        hwnd: int,
        title: str = "",
        process_name: str = "",
        pid: int = 0
    ) -> WindowInfo:
        """Retrieves detailed geometry and client bounds for a window handle."""
        if self._mock_window is not None and self._mock_window.hwnd == hwnd:
            return self._mock_window

        if sys.platform != "win32":
            raise RuntimeError("get_window_info requires Windows OS")

        import win32gui
        import win32process
        import psutil

        if not title:
            title = win32gui.GetWindowText(hwnd)
        if not pid:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
        if not process_name:
            try:
                process_name = psutil.Process(pid).name()
            except Exception:
                process_name = "unknown"

        # Window rect (including frame)
        win_rect = win32gui.GetWindowRect(hwnd)
        wx, wy, wr, wb = win_rect
        ww, wh = wr - wx, wb - wy

        # Client rect (drawable area without title bar / borders)
        client_rect = win32gui.GetClientRect(hwnd)
        cx, cy, cw, ch = client_rect
        # Map client top-left (0, 0) to screen coordinates
        pt = win32gui.ClientToScreen(hwnd, (0, 0))
        screen_cx, screen_cy = pt[0], pt[1]

        is_minimized = win32gui.IsIconic(hwnd) != 0
        foreground_hwnd = win32gui.GetForegroundWindow()
        is_focused = (foreground_hwnd == hwnd)

        return WindowInfo(
            hwnd=hwnd,
            title=title,
            process_name=process_name,
            pid=pid,
            x=wx,
            y=wy,
            width=ww,
            height=wh,
            client_x=screen_cx,
            client_y=screen_cy,
            client_width=cw,
            client_height=ch,
            is_minimized=is_minimized,
            is_focused=is_focused,
            dpi_scale=1.0,
        )

    def focus_window(self, hwnd: Optional[int] = None) -> bool:
        """Brings the target window to the foreground."""
        if self._mock_window is not None:
            self._mock_window.is_focused = True
            return True

        if sys.platform != "win32":
            return False

        import win32gui
        target_hwnd = hwnd or (self._current_window.hwnd if self._current_window else None)
        if not target_hwnd or not win32gui.IsWindow(target_hwnd):
            return False

        try:
            if win32gui.IsIconic(target_hwnd):
                import win32con
                win32gui.ShowWindow(target_hwnd, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(target_hwnd)
            return True
        except Exception as e:
            logger.warning("Could not focus window %s: %s", target_hwnd, e)
            return False
