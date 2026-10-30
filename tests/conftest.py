"""
tests/conftest.py - Centralized Mocking Infrastructure & Fixtures for UGM
Provides Windows API shims and headless fallbacks on Linux / Termux environments and common test fixtures.
"""

import os
import sys
import types
from unittest.mock import MagicMock

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# ============================================================================
# 1. Non-Windows System Module Shimming
# ============================================================================


def install_windows_shims():
    """
    Injects realistic mock modules into sys.modules when running on non-Windows
    platforms (Linux, macOS, Android Termux) so that UGM modules import cleanly.
    """
    # ------------------------------------------------------------------------
    # numpy Mock (if missing on minimal Linux / Termux environment)
    # ------------------------------------------------------------------------
    if "numpy" not in sys.modules:
        try:
            import numpy  # noqa: F401
        except ImportError:
            m_numpy = types.ModuleType("numpy")

            class MockNdArray:
                def __init__(self, shape=(1080, 1920, 3), dtype=None):
                    self.shape = tuple(shape)
                    self.dtype = dtype or "uint8"

                def copy(self):
                    return MockNdArray(self.shape, self.dtype)

                def __getitem__(self, item):
                    h = self.shape[0] if len(self.shape) > 0 else 1
                    w = self.shape[1] if len(self.shape) > 1 else 1
                    c = self.shape[2] if len(self.shape) > 2 else 1
                    if isinstance(item, tuple) and len(item) >= 2:
                        s_y, s_x = item[0], item[1]
                        new_h = (s_y.stop - s_y.start) if (isinstance(s_y, slice) and s_y.stop is not None and s_y.start is not None) else h
                        new_w = (s_x.stop - s_x.start) if (isinstance(s_x, slice) and s_x.stop is not None and s_x.start is not None) else w
                        return MockNdArray(shape=(int(max(1, new_h)), int(max(1, new_w)), c), dtype=self.dtype)
                    return self

                def __mul__(self, other):
                    return self

                def __rmul__(self, other):
                    return self

            m_numpy.ndarray = MockNdArray
            m_numpy.zeros = lambda shape, dtype=None: MockNdArray(shape, dtype)
            m_numpy.ones = lambda shape, dtype=None: MockNdArray(shape, dtype)
            m_numpy.array = lambda arr, dtype=None: MockNdArray(getattr(arr, "shape", (1080, 1920, 3)), dtype)
            m_numpy.uint8 = "uint8"
            m_numpy.frombuffer = lambda buf, dtype=None: MockNdArray((1080, 1920, 4), dtype)
            sys.modules["numpy"] = m_numpy

    # ------------------------------------------------------------------------
    # cv2 Mock (if missing on minimal Linux / Termux environment)
    # ------------------------------------------------------------------------
    if "cv2" not in sys.modules:
        try:
            import cv2  # noqa: F401
        except ImportError:
            m_cv2 = types.ModuleType("cv2")
            DUMMY_PNG_HEADER = (
                b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06"
                b"\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01"
                b"\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
            )

            def mock_imwrite(path, image):
                try:
                    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
                    with open(path, "wb") as f:
                        f.write(DUMMY_PNG_HEADER)
                    return True
                except Exception:
                    return False

            class EncodedBuffer:
                def tobytes(self):
                    return DUMMY_PNG_HEADER * 30

            def mock_imencode(ext, image):
                return True, EncodedBuffer()

            def mock_imread(file_path):
                if not os.path.exists(file_path):
                    return None
                np_mod = sys.modules.get("numpy")
                return np_mod.ndarray((1080, 1920, 3)) if np_mod else None

            m_cv2.imwrite = mock_imwrite
            m_cv2.imencode = mock_imencode
            m_cv2.imread = mock_imread
            m_cv2.COLOR_BGRA2BGR = 4
            m_cv2.IMREAD_UNCHANGED = -1
            m_cv2.cvtColor = lambda img, code: img
            sys.modules["cv2"] = m_cv2

    # ------------------------------------------------------------------------
    # mss Mock (if missing)
    # ------------------------------------------------------------------------
    if "mss" not in sys.modules:
        try:
            import mss  # noqa: F401
        except ImportError:
            m_mss = types.ModuleType("mss")

            class MockMSS:
                monitors = [{}, {"top": 0, "left": 0, "width": 1920, "height": 1080}]

                def grab(self, mon):
                    np_mod = sys.modules.get("numpy")
                    return np_mod.ndarray((1080, 1920, 4)) if np_mod else None

            m_mss.mss = lambda: MockMSS()
            sys.modules["mss"] = m_mss

    # ------------------------------------------------------------------------
    # mcp Mock (if missing on host OS)
    # ------------------------------------------------------------------------
    if "mcp" not in sys.modules:
        m_mcp = types.ModuleType("mcp")
        m_server = types.ModuleType("mcp.server")
        m_mcpserver = types.ModuleType("mcp.server.mcpserver")

        class MockMCPServer:
            def __init__(self, name="universal-game-mcp", version="1.5.0", instructions=""):
                self.name = name
                self.version = version
                self.instructions = instructions
                self._tools = {}

            def tool(self, *args, **kwargs):
                def decorator(fn):
                    self._tools[fn.__name__] = fn
                    return fn
                return decorator

            async def call_tool(self, name, arguments=None):
                import json
                import inspect
                fn = self._tools.get(name)
                if not fn:
                    raise ValueError(f"Tool {name} not found")
                args = arguments or {}
                res = fn(**args)
                if inspect.isawaitable(res):
                    res = await res
                return type("ToolResult", (), {
                    "structured_content": {"result": res} if isinstance(res, dict) else res,
                    "content": [type("Content", (), {"text": json.dumps(res) if isinstance(res, dict) else str(res)})()]
                })()

        m_mcpserver.MCPServer = MockMCPServer
        m_server.mcpserver = m_mcpserver
        m_mcp.server = m_server
        sys.modules["mcp"] = m_mcp
        sys.modules["mcp.server"] = m_server
        sys.modules["mcp.server.mcpserver"] = m_mcpserver

    # ------------------------------------------------------------------------
    # psutil Mock (if missing on host OS)
    # ------------------------------------------------------------------------
    if "psutil" not in sys.modules:
        try:
            import psutil  # noqa: F401
        except ImportError:
            m_psutil = types.ModuleType("psutil")

            class NoSuchProcess(Exception):
                pass

            class AccessDenied(Exception):
                pass

            class ZombieProcess(Exception):
                pass

            class MockProcess:
                def __init__(self, pid=1000, name="hoi4.exe"):
                    self.pid = pid
                    self._name = name
                    self.info = {"name": name, "pid": pid}

                def name(self):
                    return self._name

            m_psutil.NoSuchProcess = NoSuchProcess
            m_psutil.AccessDenied = AccessDenied
            m_psutil.ZombieProcess = ZombieProcess
            m_psutil.Process = lambda pid=1000: MockProcess(pid)
            m_psutil.process_iter = lambda attrs=None: [MockProcess(1001, "hoi4.exe"), MockProcess(1002, "explorer.exe")]
            sys.modules["psutil"] = m_psutil

    if sys.platform == "win32":
        return

    # ------------------------------------------------------------------------
    # win32con Mock
    # ------------------------------------------------------------------------
    if "win32con" not in sys.modules:
        m_win32con = types.ModuleType("win32con")
        m_win32con.SW_RESTORE = 9
        m_win32con.SRCCOPY = 0x00CC0020
        sys.modules["win32con"] = m_win32con

    # ------------------------------------------------------------------------
    # win32gui Mock
    # ------------------------------------------------------------------------
    if "win32gui" not in sys.modules:
        m_win32gui = types.ModuleType("win32gui")

        _mock_windows = {}

        def mock_is_window(hwnd):
            return hwnd is not None and int(hwnd) > 0

        def mock_is_window_visible(hwnd):
            return True

        def mock_get_window_text(hwnd):
            return _mock_windows.get(hwnd, {}).get("title", "Hearts of Iron IV")

        def mock_enum_windows(callback, extra):
            default_windows = [
                (1001, "Hearts of Iron IV"),
                (1002, "Calculator"),
                (1003, "Explorer")
            ]
            for hwnd, title in default_windows:
                _mock_windows[hwnd] = {"title": title}
                if not callback(hwnd, extra):
                    break

        def mock_get_window_rect(hwnd):
            return (0, 0, 1920, 1080)

        def mock_get_client_rect(hwnd):
            return (0, 0, 1920, 1080)

        def mock_client_to_screen(hwnd, pt):
            return (0, 0)

        def mock_is_iconic(hwnd):
            return 0

        def mock_get_foreground_window():
            return 1001

        m_win32gui.IsWindow = mock_is_window
        m_win32gui.IsWindowVisible = mock_is_window_visible
        m_win32gui.GetWindowText = mock_get_window_text
        m_win32gui.EnumWindows = mock_enum_windows
        m_win32gui.GetWindowRect = mock_get_window_rect
        m_win32gui.GetClientRect = mock_get_client_rect
        m_win32gui.ClientToScreen = mock_client_to_screen
        m_win32gui.IsIconic = mock_is_iconic
        m_win32gui.GetForegroundWindow = mock_get_foreground_window
        m_win32gui.SetForegroundWindow = MagicMock(return_value=True)
        m_win32gui.ShowWindow = MagicMock(return_value=True)
        m_win32gui.GetWindowDC = MagicMock(return_value=1)
        m_win32gui.ReleaseDC = MagicMock(return_value=1)
        m_win32gui.DeleteObject = MagicMock(return_value=True)
        m_win32gui.GetDesktopWindow = MagicMock(return_value=0)

        sys.modules["win32gui"] = m_win32gui

    # ------------------------------------------------------------------------
    # win32process Mock
    # ------------------------------------------------------------------------
    if "win32process" not in sys.modules:
        m_win32proc = types.ModuleType("win32process")
        m_win32proc.GetWindowThreadProcessId = MagicMock(return_value=(1, 9999))
        sys.modules["win32process"] = m_win32proc

    # ------------------------------------------------------------------------
    # win32ui Mock
    # ------------------------------------------------------------------------
    if "win32ui" not in sys.modules:
        m_win32ui = types.ModuleType("win32ui")

        mock_dc = MagicMock()
        mock_dc.CreateCompatibleDC.return_value = mock_dc
        mock_dc.SelectObject.return_value = None
        mock_dc.BitBlt.return_value = None
        mock_dc.DeleteDC.return_value = None

        mock_bitmap = MagicMock()
        mock_bitmap.CreateCompatibleBitmap.return_value = None
        mock_bitmap.GetHandle.return_value = 1
        mock_bitmap.GetInfo.return_value = {'bmHeight': 1080, 'bmWidth': 1920}
        mock_bitmap.GetBitmapBits.return_value = b"\x00" * (1920 * 1080 * 4)

        m_win32ui.CreateDCFromHandle = MagicMock(return_value=mock_dc)
        m_win32ui.CreateBitmap = MagicMock(return_value=mock_bitmap)
        sys.modules["win32ui"] = m_win32ui

    # ------------------------------------------------------------------------
    # pydirectinput Mock
    # ------------------------------------------------------------------------
    if "pydirectinput" not in sys.modules:
        m_pydirectinput = types.ModuleType("pydirectinput")

        class FailSafeException(Exception):
            """Raised by pydirectinput when mouse moves to a screen corner."""
            pass

        m_pydirectinput.FAILSAFE = True
        m_pydirectinput.FailSafeException = FailSafeException
        m_pydirectinput.moveTo = MagicMock()
        m_pydirectinput.click = MagicMock()
        m_pydirectinput.rightClick = MagicMock()
        m_pydirectinput.middleClick = MagicMock()
        m_pydirectinput.keyDown = MagicMock()
        m_pydirectinput.keyUp = MagicMock()
        m_pydirectinput.press = MagicMock()
        m_pydirectinput.position = MagicMock(return_value=(500, 500))

        sys.modules["pydirectinput"] = m_pydirectinput

    # ------------------------------------------------------------------------
    # bettercam Mock
    # ------------------------------------------------------------------------
    if "bettercam" not in sys.modules:
        m_bettercam = types.ModuleType("bettercam")

        class MockBetterCam:
            def __init__(self):
                self._BetterCam__frame_available = None

            def release(self):
                pass

            def stop(self):
                pass

            def grab(self):
                return None

        m_sub = types.ModuleType("bettercam.bettercam")
        m_sub.BetterCam = MockBetterCam

        m_bettercam.bettercam = m_sub
        m_bettercam.create = MagicMock(return_value=MockBetterCam())
        sys.modules["bettercam"] = m_bettercam
        sys.modules["bettercam.bettercam"] = m_sub


# Execute shim installation immediately on import
install_windows_shims()

# ============================================================================
# 2. Pytest Fixtures
# ============================================================================

try:
    import pytest

    @pytest.fixture(autouse=True)
    def clean_safety():
        """Ensures the safety manager emergency stop state is clean before and after each test."""
        from ugm.core.safety import safety_manager
        safety_manager.reset_emergency_stop()
        yield
        safety_manager.reset_emergency_stop()

    @pytest.fixture
    def mock_window():
        """Standard valid mock game window for offline testing."""
        from ugm.core.types import WindowInfo
        return WindowInfo(
            hwnd=1001,
            title="Hearts of Iron IV",
            process_name="hoi4.exe",
            pid=9999,
            x=0,
            y=0,
            width=1920,
            height=1080,
            client_x=0,
            client_y=0,
            client_width=1920,
            client_height=1080,
            is_minimized=False,
            is_focused=True,
            dpi_scale=1.0
        )

    @pytest.fixture
    def unfocused_mock_window():
        """Mock window that is not currently focused by the operating system."""
        from ugm.core.types import WindowInfo
        return WindowInfo(
            hwnd=1001,
            title="Hearts of Iron IV",
            process_name="hoi4.exe",
            pid=9999,
            x=0,
            y=0,
            width=1920,
            height=1080,
            client_x=0,
            client_y=0,
            client_width=1920,
            client_height=1080,
            is_minimized=False,
            is_focused=False,
            dpi_scale=1.0
        )

except ImportError:
    pass
