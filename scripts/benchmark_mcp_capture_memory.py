"""Measure public MCP screenshot-cache memory in a fresh synthetic process."""

import argparse
import asyncio
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

import numpy as np
import psutil

from ugm.core.capture import FixtureBackend
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


async def measure(width: int, height: int, count: int) -> dict:
    fixture = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "sim_fixture.png"
    process = psutil.Process()
    with TemporaryDirectory() as tmp:
        ctx = UGMContext(offline_fixture=str(fixture), db_path=Path(tmp) / "benchmark.db")
        server = create_server(ctx)
        warmup = await server.call_tool("read_text", {})
        if warmup.is_error:
            raise RuntimeError("OCR warmup failed")
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
        final = process.memory_info().rss
        data = {
            "evidence": "synthetic_mcp_fixture",
            "platform": sys.platform,
            "client_frame": [width, height],
            "captures": count,
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
    args = parser.parse_args()
    if args.count <= 0:
        parser.error("--count must be positive")
    print(json.dumps(asyncio.run(measure(args.width, args.width * 9 // 16, args.count))))
