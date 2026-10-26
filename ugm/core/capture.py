"""
Universal Game MCP (UGM) - Screen Capture Subsystem
Provides ultra-low latency capture via BetterCam (Desktop Duplication API),
with automatic graceful fallbacks to Win32 GDI/MSS and FixtureBackend for offline tests.
"""

from __future__ import annotations
import os
import sys
import time
import base64
import logging
from abc import ABC, abstractmethod
from typing import Optional, Tuple, Any
import numpy as np
import cv2

from .types import WindowInfo, NormalizedRect, BoundingBox

logger = logging.getLogger("ugm.capture")


class BaseCaptureBackend(ABC):
    """Abstract interface for screen capture implementations."""

    @abstractmethod
    def capture(
        self,
        window: Optional[WindowInfo] = None,
        roi: Optional[NormalizedRect] = None
    ) -> np.ndarray:
        """Captures a frame as a BGR numpy ndarray."""
        pass

    def release(self) -> None:
        """Cleans up capture resources."""
        pass


class BetterCamBackend(BaseCaptureBackend):
    """DirectX Desktop Duplication API capture using BetterCam."""

    def __init__(self, output_idx: int = 0):
        self.output_idx = output_idx
        self._camera = None
        self._init_camera()

    def _init_camera(self) -> None:
        try:
            import bettercam
            # Ensure safe release in case init fails or on GC cleanup
            def _safe_release(cam_self):
                try:
                    if hasattr(cam_self, "_BetterCam__frame_available") and cam_self._BetterCam__frame_available is not None:
                        cam_self.stop()
                except Exception:
                    pass
            bettercam.bettercam.BetterCam.release = _safe_release

            self._camera = bettercam.create(
                output_idx=self.output_idx,
                output_color="BGR"
            )
            logger.info("BetterCam initialized on display %d", self.output_idx)
        except Exception as e:
            logger.warning("BetterCam initialization failed: %s", e)
            self._camera = None

    def capture(
        self,
        window: Optional[WindowInfo] = None,
        roi: Optional[NormalizedRect] = None
    ) -> np.ndarray:
        if self._camera is None:
            raise RuntimeError("BetterCam is not available")

        frame = self._camera.grab()
        if frame is None:
            # BetterCam grab might return None if frame hasn't changed; retry once
            time.sleep(0.01)
            frame = self._camera.grab()
            if frame is None:
                raise RuntimeError("BetterCam returned empty frame")

        # If window is specified, crop to client rect
        if window is not None:
            cx, cy = max(0, window.client_x), max(0, window.client_y)
            cw, ch = window.client_width, window.client_height
            h, w = frame.shape[:2]
            cx2 = min(w, cx + cw)
            cy2 = min(h, cy + ch)
            frame = frame[cy:cy2, cx:cx2]

        # If ROI is specified, crop ROI
        if roi is not None:
            h, w = frame.shape[:2]
            bbox = roi.to_pixel(w, h)
            frame = frame[bbox.y:bbox.bottom, bbox.x:bbox.right]

        return frame

    def release(self) -> None:
        if self._camera is not None:
            try:
                self._camera.release()
            except Exception:
                pass
            self._camera = None


class MSSBackend(BaseCaptureBackend):
    """Fallback capture using MSS."""

    def __init__(self):
        self._sct = None

    def _get_sct(self):
        if self._sct is None:
            import mss
            self._sct = mss.mss()
        return self._sct

    def capture(
        self,
        window: Optional[WindowInfo] = None,
        roi: Optional[NormalizedRect] = None
    ) -> np.ndarray:
        sct = self._get_sct()
        if window is not None:
            monitor = {
                "top": window.client_y,
                "left": window.client_x,
                "width": window.client_width,
                "height": window.client_height,
            }
        else:
            monitor = sct.monitors[1]

        shot = sct.grab(monitor)
        # Convert BGRA to BGR
        frame = np.array(shot, dtype=np.uint8)[:, :, :3]

        if roi is not None:
            h, w = frame.shape[:2]
            bbox = roi.to_pixel(w, h)
            frame = frame[bbox.y:bbox.bottom, bbox.x:bbox.right]

        return frame

    def release(self) -> None:
        if self._sct is not None:
            try:
                self._sct.close()
            except Exception:
                pass
            self._sct = None


class Win32GdiBackend(BaseCaptureBackend):
    """Fallback window capture using Win32 PrintWindow / BitBlt."""

    def capture(
        self,
        window: Optional[WindowInfo] = None,
        roi: Optional[NormalizedRect] = None
    ) -> np.ndarray:
        if sys.platform != "win32":
            raise RuntimeError("Win32GdiBackend requires Windows")

        import win32gui
        import win32ui
        import win32con
        import ctypes

        hwnd = window.hwnd if window else win32gui.GetDesktopWindow()
        client_rect = win32gui.GetClientRect(hwnd)
        cw = client_rect[2] - client_rect[0]
        ch = client_rect[3] - client_rect[1]

        if cw <= 0 or ch <= 0:
            raise RuntimeError(f"Invalid window dimensions: {cw}x{ch}")

        hwnd_dc = win32gui.GetWindowDC(hwnd)
        mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
        save_dc = mfc_dc.CreateCompatibleDC()

        save_bitmap = win32ui.CreateBitmap()
        save_bitmap.CreateCompatibleBitmap(mfc_dc, cw, ch)
        save_dc.SelectObject(save_bitmap)

        # Try PrintWindow with PW_RENDERFULLCONTENT (flag 2)
        result = ctypes.windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), 2)
        if not result:
            # Fallback to BitBlt
            save_dc.BitBlt((0, 0), (cw, ch), mfc_dc, (0, 0), win32con.SRCCOPY)

        bmp_info = save_bitmap.GetInfo()
        bmp_str = save_bitmap.GetBitmapBits(True)
        img = np.frombuffer(bmp_str, dtype=np.uint8).reshape((bmp_info['bmHeight'], bmp_info['bmWidth'], 4))

        # Cleanup GDI handles
        win32gui.DeleteObject(save_bitmap.GetHandle())
        save_dc.DeleteDC()
        mfc_dc.DeleteDC()
        win32gui.ReleaseDC(hwnd, hwnd_dc)

        # Convert BGRA to BGR
        frame = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)

        if roi is not None:
            h, w = frame.shape[:2]
            bbox = roi.to_pixel(w, h)
            frame = frame[bbox.y:bbox.bottom, bbox.x:bbox.right]

        return frame


class FixtureBackend(BaseCaptureBackend):
    """
    Mock capture backend for deterministic offline testing and CI workflows.
    Allows loading static fixture images without requiring an active game.
    """

    def __init__(self, default_image: Optional[np.ndarray] = None):
        self._current_frame: Optional[np.ndarray] = default_image
        if self._current_frame is None:
            # Create a 1920x1080 synthetic frame by default
            self._current_frame = np.zeros((1080, 1920, 3), dtype=np.uint8)

    def set_frame(self, frame: np.ndarray) -> None:
        self._current_frame = frame

    def load_from_file(self, file_path: str) -> None:
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Fixture file not found: {file_path}")
        img = cv2.imread(file_path)
        if img is None:
            raise ValueError(f"Failed to decode image from {file_path}")
        self._current_frame = img

    def capture(
        self,
        window: Optional[WindowInfo] = None,
        roi: Optional[NormalizedRect] = None
    ) -> np.ndarray:
        if self._current_frame is None:
            raise RuntimeError("FixtureBackend has no frame loaded")

        frame = self._current_frame.copy()

        if roi is not None:
            h, w = frame.shape[:2]
            bbox = roi.to_pixel(w, h)
            frame = frame[bbox.y:bbox.bottom, bbox.x:bbox.right]

        return frame


class CaptureManager:
    """
    Coordinates capture backends with automatic viable fallbacks and ROI extraction.
    Fallbacks prioritize live capture backends (BetterCam -> Win32GDI -> MSS) before
    falling back to FixtureBackend.
    """

    def __init__(
        self,
        backend_override: Optional[BaseCaptureBackend] = None,
        allow_fixture_fallback: bool = True
    ):
        self.allow_fixture_fallback = allow_fixture_fallback
        self._backend: BaseCaptureBackend
        if backend_override is not None:
            self._backend = backend_override
        else:
            self._backend = self._init_preferred_backend()

        self._last_frame: Optional[np.ndarray] = None
        self._last_capture_time: float = 0.0

    def _init_preferred_backend(self) -> BaseCaptureBackend:
        # Try BetterCam first
        try:
            backend = BetterCamBackend()
            if backend._camera is not None:
                return backend
        except Exception as e:
            logger.debug("BetterCam not available: %s", e)

        # Try Win32 GDI next on Windows
        if sys.platform == "win32":
            try:
                return Win32GdiBackend()
            except Exception as e:
                logger.debug("Win32 GDI not available: %s", e)

        # Fallback to MSS
        try:
            return MSSBackend()
        except Exception as e:
            logger.debug("MSS not available: %s", e)

        # Ultimate fallback for tests/headless
        if self.allow_fixture_fallback:
            logger.info("Defaulting to FixtureBackend (offline/headless mode)")
            return FixtureBackend()
        raise RuntimeError("No live screen capture backends available.")

    def _try_next_viable_backend(self) -> Optional[BaseCaptureBackend]:
        """Tries the next viable live capture backend before considering mock fixture."""
        if isinstance(self._backend, BetterCamBackend):
            if sys.platform == "win32":
                try:
                    logger.info("Falling back from BetterCam to Win32GdiBackend...")
                    return Win32GdiBackend()
                except Exception as e:
                    logger.debug("Win32GDI fallback init failed: %s", e)
            try:
                logger.info("Falling back to MSSBackend...")
                return MSSBackend()
            except Exception as e:
                logger.debug("MSS fallback init failed: %s", e)
        elif isinstance(self._backend, Win32GdiBackend):
            try:
                logger.info("Falling back from Win32GdiBackend to MSSBackend...")
                return MSSBackend()
            except Exception as e:
                logger.debug("MSS fallback init failed: %s", e)

        if self.allow_fixture_fallback and not isinstance(self._backend, FixtureBackend):
            logger.warning("All viable live capture backends failed. Falling back to FixtureBackend.")
            return FixtureBackend()
        return None

    @property
    def backend(self) -> BaseCaptureBackend:
        return self._backend

    def set_backend(self, backend: BaseCaptureBackend) -> None:
        if self._backend is not None and self._backend is not backend:
            self._backend.release()
        self._backend = backend

    def capture(
        self,
        window: Optional[WindowInfo] = None,
        roi: Optional[NormalizedRect] = None
    ) -> np.ndarray:
        """
        Captures a frame from the target window and optional ROI.
        Attempts viable live backends on failure before falling back to fixture.
        Caches the captured frame and timestamp.
        """
        t0 = time.perf_counter()
        while True:
            try:
                frame = self._backend.capture(window=window, roi=roi)
                break
            except Exception as exc:
                logger.warning("Capture backend %s failed (%s). Attempting viable fallback.", type(self._backend).__name__, exc)
                next_backend = self._try_next_viable_backend()
                if next_backend is not None:
                    self.set_backend(next_backend)
                else:
                    raise RuntimeError(f"All viable capture backends failed. Last error: {exc}") from exc

        self._last_frame = frame
        self._last_capture_time = time.perf_counter() - t0
        return frame

    @property
    def last_frame(self) -> Optional[np.ndarray]:
        return self._last_frame

    @property
    def last_latency_ms(self) -> float:
        return self._last_capture_time * 1000.0

    @staticmethod
    def encode_base64_png(image: np.ndarray) -> str:
        """Encodes an OpenCV image to a base64 PNG string."""
        success, encoded = cv2.imencode(".png", image)
        if not success:
            raise ValueError("Could not encode image to PNG")
        return base64.b64encode(encoded.tobytes()).decode("utf-8")

    @staticmethod
    def save_image(image: np.ndarray, file_path: str) -> str:
        """Saves an image to disk and ensures parent directories exist."""
        os.makedirs(os.path.dirname(os.path.abspath(file_path)), exist_ok=True)
        cv2.imwrite(file_path, image)
        return os.path.abspath(file_path)
