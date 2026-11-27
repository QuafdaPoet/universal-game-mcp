"""Profile RapidOCR stages in an offline fixture subprocess, not a real game."""
from __future__ import annotations

import argparse
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import time

import psutil

from scripts.benchmark_mcp_capture_memory import GOLD_ROI, process_peak_bytes
from ugm.core.types import NormalizedRect
from ugm.server import UGMContext


class StageProbe:
    def __init__(self, target, name, observations, memory):
        self.target, self.name = target, name
        self.observations, self.memory = observations, memory

    def __getattr__(self, name):
        return getattr(self.target, name)

    def __call__(self, *args, **kwargs):
        before = self.memory()
        start = time.perf_counter()
        result = self.target(*args, **kwargs)
        elapsed = (time.perf_counter() - start) * 1000
        after = self.memory()
        self.observations.append({
            "stage": self.name, "before": before, "after": after, "elapsed_ms": round(elapsed, 3),
            "peak_increase_bytes": after["os_peak_bytes"] - before["os_peak_bytes"],
        })
        return result


def dominant_stage(stages):
    """Largest measured new process high-water mark, not allocator attribution."""
    new_peaks = (stage for stage in stages if stage["peak_increase_bytes"] > 0)
    return max(new_peaks, key=lambda stage: stage["peak_increase_bytes"], default=None)


def measure(calls, threads, region):
    package = version("rapidocr-onnxruntime")
    if package != "1.4.4":
        raise RuntimeError(f"Stage probe expects RapidOCR 1.4.4, got {package}.")
    process = psutil.Process()

    def memory():
        return {"rss_bytes": process.memory_info().rss, "os_peak_bytes": process_peak_bytes(process)}

    fixture = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "sim_fixture.png"
    with TemporaryDirectory() as tmp:
        ctx = UGMContext(offline_fixture=str(fixture), db_path=Path(tmp) / "profile.db",
                         ocr_intra_op_threads=threads)
        try:
            engine = ctx.ocr_engine
            wrapped = engine._engine
            stages = []
            for method, stage in (
                ("preprocess", "global_resize"),
                ("maybe_add_letterbox", "letterbox"),
                ("auto_text_det", "text_detection"),
                ("get_crop_img_list", "text_crop"),
                ("text_rec", "text_recognition"),
            ):
                original = getattr(wrapped, method)
                setattr(wrapped, method, StageProbe(original, stage, stages, memory))

            phase_context = memory()
            roi = NormalizedRect(*GOLD_ROI) if region == "gold" else None
            results = []
            previous_digest = None
            for _ in range(calls):
                start_phase = memory()
                stages.clear()
                frame = ctx.capture_observation()
                out = engine.read_text(frame, roi=roi, event_tag="profile")
                geometry = sorted((it.text, it.bbox_normalized.x, it.bbox_normalized.y,
                                   it.bbox_normalized.width, it.bbox_normalized.height) for it in out.items)
                if sum("".join(text.upper().split()) == "GOLD:500" for text, *_ in geometry) != 1:
                    raise RuntimeError("Expected a single intact fixture OCR label GOLD:500.")
                digest = hashlib.sha256(json.dumps(geometry).encode()).hexdigest()
                if previous_digest is not None and previous_digest != digest:
                    raise RuntimeError("Repeated fixture OCR text or geometry drifted.")
                previous_digest = digest
                peak = dominant_stage(stages)
                results.append({
                    "before": start_phase, "after": memory(), "ocr_ms": out.latency_ms,
                    "stages": stages.copy(),
                    "largest_measured_peak_rise": peak["stage"] if peak is not None else None,
                    "output_sha256": digest,
                })
            return {
                "evidence": "synthetic_rapidocr_stage_profile",
                "region": region, "threads": engine.effective_intra_op_threads,
                "rapidocr_version": package, "onnxruntime_version": version("onnxruntime"),
                "after_context": phase_context, "calls": results,
            }
        finally:
            ctx.storage.db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--calls", type=int, choices=(1, 4), default=1)
    parser.add_argument("--threads", type=int, choices=(-1, 2, 4), default=2)
    parser.add_argument("--region", choices=("full", "gold"), default="full")
    args = parser.parse_args()
    print(json.dumps(measure(args.calls, args.threads, args.region)))


if __name__ == "__main__":
    main()
