"""Measure public MCP screenshot-cache memory in a fresh synthetic process."""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import sys
from statistics import median
from tempfile import TemporaryDirectory

import numpy as np
import psutil

from ugm.core.capture import FixtureBackend
from ugm.core.types import NormalizedRect
from ugm.server import UGMContext, create_server


def process_peak_bytes(process: psutil.Process) -> int:
    if sys.platform == "win32":
        return process.memory_info().peak_wset
    if sys.platform.startswith("linux"):
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
    raise RuntimeError("OS peak RSS measurement is supported on Windows and Linux.")


class FreshFrames(FixtureBackend):
    def __init__(self, width: int, height: int):
        super().__init__(np.zeros((1, 1, 3), dtype=np.uint8))
        self.shape = (height, width, 3)
        self.value = 0

    def capture(self, window=None, roi=None):
        self.value = (self.value + 1) % 256
        return np.full(self.shape, self.value, dtype=np.uint8)


# Padded rectangle around GOLD: 500 in the 900x550 simulator fixture.
GOLD_ROI = [0.22, 0.0, 0.17, 0.15]


async def measure(width: int, height: int, count: int, ocr_threads: int,
                  ocr_region: str = "full") -> dict:
    fixture = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "sim_fixture.png"
    process = psutil.Process()
    def memory():
        return {"rss_bytes": process.memory_info().rss, "os_peak_bytes": process_peak_bytes(process)}

    phases = {"after_imports": memory()}
    with TemporaryDirectory() as tmp:
        ctx = UGMContext(offline_fixture=str(fixture), db_path=Path(tmp) / "benchmark.db",
                         ocr_intra_op_threads=ocr_threads)
        server = create_server(ctx)
        phases["after_context"] = memory()
        fixture_window = ctx.window_manager.current_window
        ocr_width, ocr_height = fixture_window.client_width, fixture_window.client_height
        roi = GOLD_ROI if ocr_region == "gold" else None
        rect = NormalizedRect(*roi).to_pixel(ocr_width, ocr_height) if roi else None
        source_size = [rect.width, rect.height] if rect else [ocr_width, ocr_height]

        async def read():
            result = await server.call_tool("read_text", {"roi": roi} if roi else {})
            if result.is_error:
                raise RuntimeError("OCR failed")
            out = result.structured_content.get("result", result.structured_content)
            gold = [item for item in out["items"]
                    if "".join(item["text"].upper().split()) == "GOLD:500"]
            if len(gold) != 1:
                raise RuntimeError(f"Expected one unambiguous GOLD:500 item, got {out['full_text']!r}")
            box = gold[0]["bbox_normalized"]
            x, y, w, h = (box[key] for key in ("x", "y", "width", "height"))
            if not (0.22 <= x < 0.28 and 0.01 <= y < 0.055
                    and 0.065 < w < 0.13 and 0.012 < h < 0.06
                    and x + w <= 0.39 and y + h <= 0.15):
                raise RuntimeError(f"Gold box is not close to the label in full-frame coordinates: {box}")
            boxes = sorted(
                (item["text"], tuple(sorted(item["bbox_normalized"].items())))
                for item in out["items"]
            )
            return out["latency_ms"], hashlib.sha256(json.dumps(boxes).encode()).hexdigest()

        first_latency, geometry = await read()
        phases["after_first_ocr"] = memory()
        repeated = [await read() for _ in range(3)]
        if any(digest != geometry for _, digest in repeated):
            raise RuntimeError("Fixture text or normalized boxes changed across repeated OCR calls")
        phases["after_repeated_ocr"] = memory()
        ctx.capture_manager.set_backend(FreshFrames(width, height))
        initial = process.memory_info().rss
        max_cache_bytes = 0
        for _ in range(count):
            result = await server.call_tool("capture_screen", {"roi": [0, 0, 0.01, 0.01]})
            if result.is_error:
                raise RuntimeError("Synthetic capture failed")
            max_cache_bytes = max(max_cache_bytes, sum(
                entry.frame.nbytes for entry in ctx.pre_capture_cache.values()
            ))
        phases["after_capture_loop"] = memory()
        final = phases["after_capture_loop"]["rss_bytes"]
        data = {
            "evidence": "synthetic_mcp_fixture",
            "platform": sys.platform,
            "client_frame": [width, height],
            "captures": count,
            "ocr_fixture_frame": [ocr_width, ocr_height],
            "ocr_source_size": source_size,
            "ocr_roi_normalized": roi,
            "ocr_threads_requested": ocr_threads,
            "ocr_threads_effective": ctx.ocr_engine.effective_intra_op_threads,
            "logical_cpus": psutil.cpu_count(),
            "ocr_first_ms": first_latency,
            "ocr_repeated_median_ms": median(ms for ms, _ in repeated),
            "ocr_text_and_boxes_sha256": geometry,
            "phases": phases,
            "cache_entries_final": len(ctx.pre_capture_cache),
            "cache_bytes_max": max_cache_bytes,
            "rss_before_captures_bytes": initial,
            "rss_final_bytes": final,
            "os_peak_rss_bytes": process_peak_bytes(process),
        }
        ctx.storage.db.close()
        return data


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--width", type=int, choices=(1920, 3840), required=True)
    parser.add_argument("--count", type=int, default=12)
    parser.add_argument("--ocr-threads", type=int, choices=(-1, 2, 4), default=4,
                        help="-1 uses ONNX Runtime automatic selection")
    parser.add_argument("--ocr-region", choices=("full", "gold"), default="full",
                        help="Use the full simulator input or a padded region around GOLD:500")
    args = parser.parse_args()
    if args.count <= 0:
        parser.error("--count must be positive")
    print(json.dumps(asyncio.run(measure(
        args.width, args.width * 9 // 16, args.count, args.ocr_threads, args.ocr_region
    ))))
