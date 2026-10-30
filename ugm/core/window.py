"""
Universal Game MCP (UGM) - Window Management
Provides DPI-aware window detection, geometry queries, focus management,
and mocking capabilities for headless unit testing.
"""

from __future__ import annotations
import sys
import re
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


class WindowManager:
    """Manages game window discovery, rect mapping, and focus control."""

    def __init__(self, mock_window: Optional[WindowInfo] = None):
        self._mock_window = mock_window
        self._current_window: Optional[WindowInfo] = mock_window

    def set_mock_window(self, mock: Optional[WindowInfo]) -> None:
        """Sets a mock window for testing in headless or virtual environments."""
        self._mock_window = mock
        self._current_window = mock

    @property
    def current_window(self) -> Optional[WindowInfo]:
        if self._mock_window is not None:
            return self._mock_window
        if self._current_window is not None and sys.platform == "win32":
            try:
                import win32gui
                if win32gui.IsWindow(self._current_window.hwnd):
                    self._current_window = self.get_window_info(self._current_window.hwnd)
            except Exception:
                pass
        return self._current_window

    def refresh_current_window(self) -> Optional[WindowInfo]:
        """Explicitly re-queries the window geometry and state from the OS."""
        return self.current_window

    def find_window(
        self,
        window_title: Optional[str] = None,
        process_name: Optional[str] = None
    ) -> Optional[WindowInfo]:
        """
        Locates a game window matching title regex/substring or process executable name.
        """
        if self._mock_window is not None:
            self._current_window = self._mock_window
            return self._mock_window

        if sys.platform != "win32":
            logger.warning("Native window management is only supported on Windows.")
            return None

        import win32gui
        import win32process
        import psutil

        matches: List[Tuple[int, str, str, int]] = []

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

                title_matched = False
                if window_title:
                    try:
                        pattern = re.escape(window_title)
                        if re.search(pattern, title, re.IGNORECASE):
                            title_matched = True
                    except re.error:
                        # Fallback to case-insensitive literal substring match
                        if window_title.lower() in title.lower():
                            title_matched = True
                else:
                    title_matched = True

                proc_matched = False
                if process_name:
                    if process_name.lower() in pname.lower():
                        proc_matched = True
                else:
                    proc_matched = True

                if title_matched and proc_matched:
                    matches.append((hwnd, title, pname, pid))
                return True
            except Exception as e:
                logger.debug("Exception in enum_windows_callback for hwnd %s: %s", hwnd, e)
                return True

        win32gui.EnumWindows(enum_windows_callback, None)

        if not matches:
            logger.warning("No window found matching title='%s', process='%s'", window_title, process_name)
            return None

        # Pick best match (prefer foreground or first visible match)
        hwnd, title, pname, pid = matches[0]

        # Security check: verify no blacklisted anti-cheat
        safety_manager.validate_process(pname)

        info = self.get_window_info(hwnd, title=title, process_name=pname, pid=pid)
        self._current_window = info
        return info

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
