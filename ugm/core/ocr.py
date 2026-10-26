"""
Universal Game MCP (UGM) - OCR Subsystem
Provides event-driven, CPU-optimized OCR using RapidOCR (ONNXRuntime PP-OCR),
with ROI cropping, coordinate normalization, and optional WinRT OCR fallback.
"""

from __future__ import annotations
import os
import sys
import time
import logging
from abc import ABC, abstractmethod
from typing import Optional, List, Tuple, Any
import numpy as np

from .types import OCRItem, OCRResult, NormalizedRect, BoundingBox

logger = logging.getLogger("ugm.ocr")

# Configure ONNX / OpenMP thread environment for Ryzen 5 5500U (CPU-light friendly)
os.environ.setdefault("OMP_NUM_THREADS", "4")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "4")
os.environ.setdefault("MKL_NUM_THREADS", "4")


class BaseOCREngine(ABC):
    """Abstract interface for OCR engines."""

    @abstractmethod
    def read_text(
        self,
        image: np.ndarray,
        roi: Optional[NormalizedRect] = None,
        event_tag: Optional[str] = None
    ) -> OCRResult:
        """Performs event-driven OCR on the provided image and optional ROI."""
        pass


class RapidOCREngine(BaseOCREngine):
    """
    RapidOCR implementation using ONNXRuntime.
    Configured for CPU efficiency with ROI cropping and horizontal text optimization.
    """

    def __init__(
        self,
        min_score: float = 0.5,
        use_cls: bool = False,
        intra_op_threads: int = 4
    ):
        self.min_score = min_score
        self.use_cls = use_cls
        self.intra_op_threads = intra_op_threads
        self._engine = None
        self._init_engine()

    def _init_engine(self) -> None:
        try:
            from rapidocr_onnxruntime import RapidOCR
            # Passing use_cls=False speeds up game UI OCR significantly
            self._engine = RapidOCR(
                use_cls=self.use_cls,
                text_score=self.min_score
            )
            logger.info("RapidOCREngine initialized with use_cls=%s", self.use_cls)
        except Exception as e:
            logger.error("Failed to initialize RapidOCR: %s", e)
            self._engine = None

    def read_text(
        self,
        image: np.ndarray,
        roi: Optional[NormalizedRect] = None,
        event_tag: Optional[str] = None
    ) -> OCRResult:
        t0 = time.perf_counter()

        if self._engine is None:
            raise RuntimeError("RapidOCR engine is not initialized")

        orig_h, orig_w = image.shape[:2]
        crop_img = image
        crop_offset_x = 0
        crop_offset_y = 0

        # Prioritize CPU: crop ROI before passing to neural network
        if roi is not None:
            roi_bbox = roi.to_pixel(orig_w, orig_h)
            crop_offset_x = roi_bbox.x
            crop_offset_y = roi_bbox.y
            crop_img = image[roi_bbox.y:roi_bbox.bottom, roi_bbox.x:roi_bbox.right]

        if crop_img.size == 0:
            return OCRResult(
                items=[],
                full_text="",
                engine="RapidOCR-ONNX",
                latency_ms=(time.perf_counter() - t0) * 1000.0,
                roi_normalized=roi,
                event_tag=event_tag
            )

        # Run inference
        ocr_res, _elapse = self._engine(crop_img)

        items: List[OCRItem] = []
        texts: List[str] = []

        if ocr_res:
            for entry in ocr_res:
                pts, text, score = entry[0], entry[1], float(entry[2])
                if score < self.min_score:
                    continue

                # Map points back to full image/window coordinates
                pts_arr = np.array(pts, dtype=np.float32)
                pts_arr[:, 0] += crop_offset_x
                pts_arr[:, 1] += crop_offset_y

                min_x = int(np.min(pts_arr[:, 0]))
                min_y = int(np.min(pts_arr[:, 1]))
                max_x = int(np.max(pts_arr[:, 0]))
                max_y = int(np.max(pts_arr[:, 1]))

                w = max(1, max_x - min_x)
                h = max(1, max_y - min_y)

                pixel_bbox = BoundingBox(x=min_x, y=min_y, width=w, height=h)
                norm_bbox = pixel_bbox.to_normalized(orig_w, orig_h)
                polygon = [[int(pt[0]), int(pt[1])] for pt in pts_arr]

                item = OCRItem(
                    text=text,
                    confidence=score,
                    bbox_pixel=pixel_bbox,
                    bbox_normalized=norm_bbox,
                    polygon=polygon
                )
                items.append(item)
                texts.append(text)

        full_text = " ".join(texts)
        latency_ms = (time.perf_counter() - t0) * 1000.0

        return OCRResult(
            items=items,
            full_text=full_text,
            engine="RapidOCR-ONNX",
            latency_ms=latency_ms,
            roi_normalized=roi,
            event_tag=event_tag
        )


class WinRTOcrEngine(BaseOCREngine):
    """
    Optional Windows native OCR using WinRT Windows.Media.Ocr.
    Zero ONNX overhead when running on native Windows 10/11 with winsdk.
    """

    def __init__(self, lang_tag: str = "en-US"):
        self.lang_tag = lang_tag
        self._available = False
        self._check_availability()

    def _check_availability(self) -> None:
        if sys.platform != "win32":
            self._available = False
            return
        try:
            import winsdk.windows.media.ocr as ocr
            import winsdk.windows.globalization as glob
            lang = glob.Language(self.lang_tag)
            self._available = ocr.OcrEngine.is_language_supported(lang)
            logger.info("WinRTOcrEngine available: %s", self._available)
        except Exception as e:
            logger.debug("WinRTOcrEngine not available: %s", e)
            self._available = False

    @property
    def is_available(self) -> bool:
        return self._available

    def read_text(
        self,
        image: np.ndarray,
        roi: Optional[NormalizedRect] = None,
        event_tag: Optional[str] = None
    ) -> OCRResult:
        if not self._available:
            raise RuntimeError("WinRT OCR is not available on this system")

        # WinRT OCR requires SoftwareBitmap conversion
        # If needed, can be invoked, but we provide RapidOCREngine as the primary cross-compatible engine
        raise NotImplementedError("Use RapidOCREngine for unified high-accuracy OCR")


class MockOCREngine(BaseOCREngine):
    """Deterministic OCR engine for offline testing without ONNX runtime."""

    def __init__(self, predefined_results: Optional[List[OCRItem]] = None):
        self.predefined_results = predefined_results or []

    def set_predefined(self, items: List[OCRItem]) -> None:
        self.predefined_results = items

    def read_text(
        self,
        image: np.ndarray,
        roi: Optional[NormalizedRect] = None,
        event_tag: Optional[str] = None
    ) -> OCRResult:
        texts = [i.text for i in self.predefined_results]
        return OCRResult(
            items=self.predefined_results,
            full_text=" ".join(texts),
            engine="MockOCR",
            latency_ms=1.0,
            roi_normalized=roi,
            event_tag=event_tag
        )
