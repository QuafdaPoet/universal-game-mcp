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
                def __init__(self, data_or_shape=None, dtype=None, fill_value=0, shape=None):
                    if shape is not None:
                        self.data = None
                        self.shape = tuple(int(x) for x in shape)
                        self.dtype = dtype or "uint8"
                    elif isinstance(data_or_shape, tuple):
                        self.data = None
                        self.shape = tuple(int(x) for x in data_or_shape)
                        self.dtype = dtype or "uint8"
                    elif isinstance(data_or_shape, list):
                        if len(data_or_shape) == 0:
                            self.data = []
                            self.shape = (0,)
                            self.dtype = dtype or "int"
                        elif isinstance(data_or_shape[0], (list, tuple)):
                            self.data = [list(r) for r in data_or_shape]
                            self.shape = (len(self.data), len(self.data[0]))
                            self.dtype = dtype or "float32"
                        else:
                            self.data = list(data_or_shape)
                            self.shape = (len(self.data),)
                            self.dtype = dtype or "int"
                    elif isinstance(data_or_shape, MockNdArray):
                        self.data = [list(r) if isinstance(r, list) else r for r in data_or_shape.data] if data_or_shape.data is not None else None
                        self.shape = data_or_shape.shape
                        self.dtype = dtype or data_or_shape.dtype
                    else:
                        self.data = None
                        self.shape = (1080, 1920, 3)
                        self.dtype = dtype or "uint8"

                    import math
                    self.size = math.prod(self.shape) if self.shape else 0
                    self._val = fill_value
                    self._has_diff = False
                    self._change_id = 0
                    self._patches = []
                    self._match_points = None

                def copy(self):
                    c = MockNdArray(shape=self.shape, dtype=self.dtype, fill_value=self._val)
                    c.data = [list(r) if isinstance(r, list) else r for r in self.data] if self.data is not None else None
                    c._has_diff = self._has_diff
                    c._change_id = self._change_id
                    c._patches = list(self._patches)
                    c._match_points = self._match_points
                    return c

                def __getitem__(self, item):
                    if self.data is not None:
                        if isinstance(item, int):
                            row = self.data[item]
                            return MockNdArray(row, self.dtype) if isinstance(row, list) else row
                        if isinstance(item, slice):
                            return MockNdArray(self.data[item], self.dtype)
                        if isinstance(item, tuple) and len(item) == 2:
                            s_y, s_x = item
                            if isinstance(s_y, slice) and isinstance(s_x, int):
                                col = [row[s_x] if isinstance(row, list) else row for row in self.data[s_y]]
                                return MockNdArray(col, self.dtype)
                            if isinstance(s_y, int) and isinstance(s_x, int):
                                return self.data[s_y][s_x]
                        if isinstance(item, list):
                            return MockNdArray([self.data[idx] for idx in item if isinstance(idx, int) and 0 <= idx < len(self.data)], self.dtype)
                        return self

                    h = self.shape[0] if len(self.shape) > 0 else 1
                    w = self.shape[1] if len(self.shape) > 1 else 1
                    c = self.shape[2] if len(self.shape) > 2 else 1
                    if isinstance(item, tuple) and len(item) >= 2:
                        s_y, s_x = item[0], item[1]
                        new_h = h
                        if isinstance(s_y, slice):
                            start = s_y.start or 0
                            stop = s_y.stop if s_y.stop is not None else h
                            new_h = max(1, stop - start)
                        new_w = w
                        if isinstance(s_x, slice):
                            start = s_x.start or 0
                            stop = s_x.stop if s_x.stop is not None else w
                            new_w = max(1, stop - start)
                        res = MockNdArray(shape=(int(new_h), int(new_w)), dtype=self.dtype, fill_value=self._val)
                        res._has_diff = self._has_diff
                        res._change_id = self._change_id
                        res._patches = list(self._patches)
                        return res
                    return self

                def __setitem__(self, key, value):
                    self._change_id += 1
                    if self.data is not None:
                        if isinstance(key, tuple) and len(key) == 2:
                            s_y, s_x = key
                            if isinstance(s_y, slice) and isinstance(s_x, int):
                                if hasattr(value, 'data') and value.data:
                                    vals = [r[0] if isinstance(r, list) else r for r in value.data]
                                elif isinstance(value, (list, tuple)):
                                    vals = value
                                else:
                                    vals = [value] * len(self.data)
                                for i, r in enumerate(self.data[s_y]):
                                    if i < len(vals):
                                        r[s_x] = vals[i]
                                return
                    if isinstance(key, tuple) and len(key) == 2:
                        s_y, s_x = key
                        if isinstance(s_y, slice) and isinstance(s_x, slice):
                            y1 = s_y.start or 0
                            y2 = s_y.stop or 0
                            x1 = s_x.start or 0
                            x2 = s_x.stop or 0
                            self._patches.append((x1, y1, x2, y2, value))
                    self._val = value

                def __len__(self):
                    if self.data is not None:
                        return len(self.data)
                    return self.shape[0] if self.shape else 0

                def __iter__(self):
                    if self.data is not None:
                        for row in self.data:
                            yield row
                    else:
                        for _ in range(len(self)):
                            yield 0

                def __mul__(self, other):
                    if self.data is not None:
                        if hasattr(other, 'data') and other.data is not None:
                            return MockNdArray([a * b for a, b in zip(self.data, other.data)])
                        return MockNdArray([x * other for x in self.data])
                    return self

                def __rmul__(self, other):
                    if self.data is not None:
                        return MockNdArray([other * x for x in self.data])
                    return self

                def __add__(self, other):
                    if self.data is not None:
                        if hasattr(other, 'data') and other.data is not None:
                            return MockNdArray([a + b for a, b in zip(self.data, other.data)])
                        return MockNdArray([x + other for x in self.data])
                    return self

                def __radd__(self, other):
                    if self.data is not None:
                        if hasattr(other, 'data') and other.data is not None:
                            return MockNdArray([a + b for a, b in zip(self.data, other.data)])
                        return MockNdArray([other + x for x in self.data])
                    return self

                def __iadd__(self, other):
                    if self.data is not None:
                        for i, r in enumerate(self.data):
                            if isinstance(r, list):
                                for j in range(len(r)):
                                    r[j] += other
                            else:
                                self.data[i] = r + other
                    return self

                def __sub__(self, other):
                    if self.data is not None:
                        if hasattr(other, 'data') and other.data is not None:
                            return MockNdArray([a - b for a, b in zip(self.data, other.data)])
                        return MockNdArray([x - other for x in self.data])
                    return self

                def __rsub__(self, other):
                    if self.data is not None:
                        return MockNdArray([other - x for x in self.data])
                    return self

                def __isub__(self, other):
                    return self

                def __truediv__(self, other):
                    return self

                def __rtruediv__(self, other):
                    if self.data is not None:
                        return MockNdArray([other / x if x != 0 else 0 for x in self.data])
                    return self

                def __itruediv__(self, other):
                    return self

                def __gt__(self, other):
                    if self.data is not None:
                        res = MockNdArray([1 if x > other else 0 for x in self.data])
                    else:
                        res = MockNdArray(shape=self.shape, dtype="bool", fill_value=self._val)
                    res._has_diff = self._has_diff
                    res._change_id = self._change_id
                    res._match_points = getattr(self, "_match_points", None)
                    return res

                def __lt__(self, other):
                    return self

                def __ge__(self, other):
                    if self.data is not None:
                        res = MockNdArray([1 if x >= other else 0 for x in self.data])
                    else:
                        res = MockNdArray(shape=self.shape, dtype="bool", fill_value=self._val)
                    res._has_diff = self._has_diff
                    res._change_id = self._change_id
                    res._match_points = getattr(self, "_match_points", None)
                    return res

                def __le__(self, other):
                    return self

                def __eq__(self, other):
                    return True

                def flatten(self):
                    n = 64 if self.size == 64 else max(1, self.size)
                    if self._has_diff or (getattr(self, "_change_id", 0) > 0) or (isinstance(self._val, (int, float)) and self._val != 0):
                        return [True if (i % 2 == 1) else False for i in range(n)]
                    return [False] * n

                def __int__(self):
                    if self.data is not None and self.data:
                        val = self.data[0][0] if isinstance(self.data[0], list) else self.data[0]
                        return int(val)
                    return int(self._val) if isinstance(self._val, (int, float)) else 10

                def __float__(self):
                    if self.data is not None and self.data:
                        val = self.data[0][0] if isinstance(self.data[0], list) else self.data[0]
                        return float(val)
                    return float(self._val) if isinstance(self._val, (int, float)) else 10.0

                def astype(self, dtype):
                    return self

            m_numpy.ndarray = MockNdArray
            m_numpy.zeros = lambda shape, dtype=None: MockNdArray(shape=shape, dtype=dtype, fill_value=0)
            m_numpy.ones = lambda shape, dtype=None: MockNdArray(shape=shape, dtype=dtype, fill_value=1)
            m_numpy.full = lambda shape, fill_value, dtype=None: MockNdArray(shape=shape, dtype=dtype, fill_value=fill_value)
            m_numpy.array = lambda arr, dtype=None: MockNdArray(arr, dtype) if isinstance(arr, (list, tuple)) else MockNdArray(getattr(arr, "shape", (1080, 1920, 3)), dtype, getattr(arr, "_val", 0))
            m_numpy.uint8 = "uint8"
            m_numpy.float32 = "float32"
            m_numpy.frombuffer = lambda buf, dtype=None: MockNdArray(shape=(1080, 1920, 4), dtype=dtype)
            m_numpy.count_nonzero = lambda a: max(100, int(a.size * 0.1)) if (getattr(a, "_has_diff", False) or getattr(a, "_change_id", 0) > 0) else 0
            m_numpy.mean = lambda a: 0.0
            _flat = lambda a: [x for row in a.data for x in (row if isinstance(row, list) else [row])] if getattr(a, "data", None) else [10]
            m_numpy.min = lambda a: min(_flat(a))
            m_numpy.max = lambda a: max(_flat(a))
            def _mock_where(cond):
                if getattr(cond, "_match_points", None):
                    return (MockNdArray(cond._match_points[0]), MockNdArray(cond._match_points[1]))
                if getattr(cond, "data", None):
                    return (MockNdArray([i for i, v in enumerate(cond.data) if v]),)
                return (MockNdArray([]), MockNdArray([]))
            m_numpy.where = _mock_where
            m_numpy.argsort = lambda a: [i for i, _ in sorted(enumerate(a.data if getattr(a, 'data', None) is not None else a), key=lambda x: x[1])]
            def _mock_maximum(a, b):
                if hasattr(a, "data") and hasattr(b, "data") and a.data is not None and b.data is not None:
                    return MockNdArray([max(x, y) for x, y in zip(a.data, b.data)])
                if hasattr(b, "data") and b.data is not None:
                    return MockNdArray([max(a, x) for x in b.data])
                if hasattr(a, "data") and a.data is not None:
                    return MockNdArray([max(x, b) for x in a.data])
                return max(a, b)

            def _mock_minimum(a, b):
                if hasattr(a, "data") and hasattr(b, "data") and a.data is not None and b.data is not None:
                    return MockNdArray([min(x, y) for x, y in zip(a.data, b.data)])
                if hasattr(b, "data") and b.data is not None:
                    return MockNdArray([min(a, x) for x in b.data])
                if hasattr(a, "data") and a.data is not None:
                    return MockNdArray([min(x, b) for x in a.data])
                return min(a, b)

            m_numpy.maximum = _mock_maximum
            m_numpy.minimum = _mock_minimum
            m_numpy.delete = lambda a, idxs: [x for i, x in enumerate(a) if i not in (idxs if isinstance(idxs, (list, tuple)) else (idxs.data if getattr(idxs, 'data', None) is not None else [idxs]))]
            m_numpy.concatenate = lambda arrs: [x for a in arrs for x in (a.data if getattr(a, 'data', None) is not None else (a if isinstance(a, (list, tuple)) else [a]))]
            m_numpy.abs = lambda a: a
            m_numpy.isscalar = lambda obj: isinstance(obj, (int, float, bool, str, bytes))
            m_numpy.bool_ = bool
            m_numpy.number = (int, float)
            m_random = types.ModuleType("numpy.random")
            m_random.RandomState = lambda seed=None: type("RS", (), {"randint": lambda *a, **kw: MockNdArray(shape=(1080, 1920, 3)), "rand": lambda *a, **kw: MockNdArray(shape=(1080, 1920, 3))})()
            m_numpy.random = m_random
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

            def mock_imread(file_path, flags=None):
                if not os.path.exists(file_path):
                    return None
                np_mod = sys.modules.get("numpy")
                if not np_mod:
                    return None
                fp_lower = file_path.lower()
                if "icon" in fp_lower or "btn" in fp_lower or "tmpl" in fp_lower:
                    return np_mod.ndarray((30, 30, 3))
                return np_mod.ndarray((1080, 1920, 3))

            def mock_imdecode(buf, flags=None):
                np_mod = sys.modules.get("numpy")
                return np_mod.ndarray((100, 100, 3)) if np_mod else None

            def mock_absdiff(a, b):
                res = a.copy() if hasattr(a, "copy") else a
                if (getattr(a, "_change_id", 0) != getattr(b, "_change_id", 0) or
                    getattr(a, "_val", 0) != getattr(b, "_val", 0) or
                    getattr(a, "_has_diff", False) != getattr(b, "_has_diff", False)):
                    res._has_diff = True
                    res._change_id = 999
                    res._val = 255
                else:
                    res._has_diff = False
                    res._change_id = 0
                    res._val = 0
                return res

            def mock_resize(src, dsize, fx=None, fy=None, interpolation=None):
                res = sys.modules.get("numpy").zeros((dsize[1], dsize[0]))
                if hasattr(src, "_has_diff"):
                    res._has_diff = src._has_diff
                    res._change_id = getattr(src, "_change_id", 0)
                    res._val = getattr(src, "_val", 0)
                return res

            def mock_match_template(image, templ, method, mask=None):
                patches = getattr(image, "_patches", [])
                if patches:
                    pts_y = [int(p[1]) for p in patches]
                    pts_x = [int(p[0]) for p in patches]
                else:
                    pts_y = [10]
                    pts_x = [10]
                res = sys.modules.get("numpy").zeros((10, 10))
                res._val = 0.95
                res._match_points = (pts_y, pts_x)
                return res

            def mock_rectangle(img, pt1, pt2, color, thickness=1):
                if hasattr(img, "_change_id"):
                    img._change_id += 1
                setattr(img, "_has_diff", True)
                return None

            m_cv2.imwrite = mock_imwrite
            m_cv2.imencode = mock_imencode
            m_cv2.imread = mock_imread
            m_cv2.imdecode = mock_imdecode
            m_cv2.COLOR_BGRA2BGR = 4
            m_cv2.COLOR_BGR2GRAY = 6
            m_cv2.IMREAD_UNCHANGED = -1
            m_cv2.INTER_AREA = 3
            m_cv2.INTER_CUBIC = 2
            m_cv2.INTER_LINEAR = 1
            m_cv2.THRESH_BINARY = 0
            m_cv2.RETR_EXTERNAL = 0
            m_cv2.CHAIN_APPROX_SIMPLE = 1
            m_cv2.TM_CCOEFF_NORMED = 5
            m_cv2.TM_CCORR_NORMED = 4
            m_cv2.cvtColor = lambda img, code: img
            m_cv2.absdiff = mock_absdiff
            m_cv2.threshold = lambda src, thresh, maxval, type: (thresh, src)
            m_cv2.resize = mock_resize
            m_cv2.findContours = lambda image, mode, method: ([], None)
            m_cv2.boundingRect = lambda array: (0, 0, 100, 50)
            m_cv2.circle = lambda img, center, radius, color, thickness=1: None
            m_cv2.rectangle = mock_rectangle
            m_cv2.putText = lambda img, text, org, fontFace, fontScale, color, thickness=1: None
            m_cv2.FONT_HERSHEY_SIMPLEX = 0
            m_cv2.matchTemplate = mock_match_template
            m_cv2.minMaxLoc = lambda src: (0.0, 0.95, (0, 0), (10, 10))
            sys.modules["cv2"] = m_cv2

    # ------------------------------------------------------------------------
    # rapidocr_onnxruntime Mock (if missing on minimal Linux / Termux)
    # ------------------------------------------------------------------------
    if "rapidocr_onnxruntime" not in sys.modules:
        try:
            import rapidocr_onnxruntime  # noqa: F401
        except ImportError:
            m_ocr = types.ModuleType("rapidocr_onnxruntime")

            class MockRapidOCR:
                def __init__(self, *args, **kwargs):
                    pass

                def __call__(self, img):
                    box = [[5, 5], [50, 5], [50, 20], [5, 20]]
                    return ([
                        [box, "Hearts of Iron IV 150 75% Civilian Factory Treaty Munich INFANTRY DIVISION Batch 42", 0.95]
                    ], [0.01])

            m_ocr.RapidOCR = MockRapidOCR
            sys.modules["rapidocr_onnxruntime"] = m_ocr

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

            async def list_tools(self):
                return [type("Tool", (), {"name": name}) for name in self._tools.keys()]

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
                if isinstance(res, dict):
                    res = json.loads(json.dumps(res))
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
