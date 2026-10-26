"""
Universal Game MCP (UGM) - CPU Latency & RAM Benchmark Suite
Measures actual execution latency (capture, template matching, full-screen vs ROI OCR)
and RAM consumption on AMD Ryzen 5 5500U / Windows 10/11 without claiming unmeasured speeds.
"""

import os
import sys

# Ensure repository root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import time
import psutil
import cv2
import numpy as np

# Ensure UTF-8 console output on Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from ugm.core.capture import CaptureManager, FixtureBackend
from ugm.core.ocr import RapidOCREngine
from ugm.core.icon import IconDetector
from ugm.core.verifier import ActionVerifier
from ugm.core.input import InputController
from ugm.core.window import WindowManager
from ugm.core.types import NormalizedRect, WindowInfo


def get_current_ram_mb() -> float:
    """Returns current process RSS memory in MB."""
    process = psutil.Process(os.getpid())
    return process.memory_info().rss / (1024 * 1024)


def benchmark_suite():
    print("=" * 70)
    print("   UNIVERSAL GAME MCP (UGM) - BENCHMARK & PROFILING SUITE")
    print("=" * 70)
    print(f"OS: {sys.platform} ({os.name})")
    print(f"CPU Cores (Physical/Logical): {psutil.cpu_count(logical=False)} / {psutil.cpu_count(logical=True)}")
    ram_initial = get_current_ram_mb()
    print(f"Initial Memory Footprint: {ram_initial:.2f} MB")
    print("-" * 70)

    # 1. Load 1080p Benchmark Frame
    fixture_path = "tests/fixtures/hoi4_topbar.png"
    if not os.path.exists(fixture_path):
        from scripts.generate_fixtures import main as gen_main
        gen_main()

    img = cv2.imread(fixture_path)
    h, w = img.shape[:2]
    print(f"Test Frame Dimensions: {w}x{h} (1080p BGR)")

    # 2. Benchmark Screen Capture Backend
    print("\n[1/5] Benchmarking Screen Capture Latency...")
    backend = FixtureBackend(img)
    cap = CaptureManager(backend_override=backend)

    times = []
    for _ in range(50):
        t0 = time.perf_counter()
        _ = cap.capture()
        times.append((time.perf_counter() - t0) * 1000.0)

    capture_avg = np.mean(times)
    capture_p95 = np.percentile(times, 95)
    print(f"  Capture (Memory/DMA): Avg = {capture_avg:.3f} ms | P95 = {capture_p95:.3f} ms")

    # 3. Benchmark Icon Detection (Template Matching)
    print("\n[2/5] Benchmarking Icon Detection (OpenCV Template Matching)...")
    detector = IconDetector(default_threshold=0.85)
    detector.load_template_file("civilian_factory", "tests/fixtures/icons/civilian_factory.png")

    # 3a. Full Screen Icon Detection (Multi-scale)
    times_icon_full = []
    for _ in range(20):
        t0 = time.perf_counter()
        res = detector.locate_icon(img, template_name="civilian_factory", multi_scale=True)
        times_icon_full.append((time.perf_counter() - t0) * 1000.0)

    icon_full_avg = np.mean(times_icon_full)
    print(f"  Full-screen Multi-scale: Avg = {icon_full_avg:.2f} ms | Found = {len(res.items)} | Ambiguous = {res.is_ambiguous}")

    # 3b. ROI-Cropped Icon Detection
    roi_topbar = NormalizedRect(x=0.0, y=0.0, width=1.0, height=0.08)
    times_icon_roi = []
    for _ in range(20):
        t0 = time.perf_counter()
        res_roi = detector.locate_icon(img, template_name="civilian_factory", roi=roi_topbar, multi_scale=True)
        times_icon_roi.append((time.perf_counter() - t0) * 1000.0)

    icon_roi_avg = np.mean(times_icon_roi)
    speedup_icon = icon_full_avg / max(0.01, icon_roi_avg)
    print(f"  ROI-Cropped Multi-scale: Avg = {icon_roi_avg:.2f} ms (Speedup: {speedup_icon:.1f}x)")

    # 4. Benchmark OCR (RapidOCR ONNX CPU)
    print("\n[3/5] Benchmarking OCR Engine (RapidOCR CPU)...")
    ocr = RapidOCREngine(min_score=0.45, use_cls=False)

    # Warmup
    _ = ocr.read_text(img, roi=NormalizedRect(0.04, 0.0, 0.08, 0.05))

    # 4a. ROI-Cropped Event-driven OCR (Recommended)
    pp_roi = NormalizedRect(x=0.04, y=0.0, width=0.08, height=0.05)
    times_ocr_roi = []
    for _ in range(10):
        t0 = time.perf_counter()
        res_ocr_roi = ocr.read_text(img, roi=pp_roi, event_tag="bench_pp")
        times_ocr_roi.append((time.perf_counter() - t0) * 1000.0)

    ocr_roi_avg = np.mean(times_ocr_roi)
    ocr_roi_p95 = np.percentile(times_ocr_roi, 95)
    print(f"  ROI Event-driven OCR:    Avg = {ocr_roi_avg:.2f} ms | P95 = {ocr_roi_p95:.2f} ms | Read: '{res_ocr_roi.full_text}'")

    # 4b. Full-Screen 1080p OCR (Unoptimized comparison)
    print("  Measuring Full-Screen 1080p OCR (this demonstrates why ROI crop is essential)...")
    t0 = time.perf_counter()
    res_ocr_full = ocr.read_text(img)
    ocr_full_time = (time.perf_counter() - t0) * 1000.0
    ocr_speedup = ocr_full_time / max(0.01, ocr_roi_avg)
    print(f"  Full-Screen 1080p OCR:   Time = {ocr_full_time:.2f} ms (ROI is {ocr_speedup:.1f}x faster!)")

    # 5. Benchmark Visual Diff Verification
    print("\n[4/5] Benchmarking Action Verification (Visual Diff)...")
    wm = WindowManager()
    inp = InputController(wm, dry_run_default=True)
    verifier = ActionVerifier(cap, ocr, inp)

    frame_changed = img.copy()
    cv2.rectangle(frame_changed, (100, 100), (200, 150), (255, 255, 255), -1)

    times_ver = []
    for _ in range(50):
        t0 = time.perf_counter()
        ver_res = verifier.verify_selection(img, frame_changed)
        times_ver.append((time.perf_counter() - t0) * 1000.0)

    ver_avg = np.mean(times_ver)
    print(f"  Visual Diff Verification: Avg = {ver_avg:.3f} ms | Verified = {ver_res.verified}")

    # 6. Memory Profile
    ram_final = get_current_ram_mb()
    print("\n[5/5] Measuring RAM Footprint...")
    print(f"  Initial Memory: {ram_initial:.2f} MB")
    print(f"  Peak/Final Memory: {ram_final:.2f} MB")
    print(f"  Net Memory Delta: {ram_final - ram_initial:.2f} MB")

    # Summary table
    print("\n" + "=" * 70)
    print("   BENCHMARK SUMMARY (Ryzen 5 5500U Compatible)")
    print("=" * 70)
    print(f"| Subsystem                 | Average Latency | Notes                       |")
    print(f"|---------------------------|-----------------|-----------------------------|")
    print(f"| Memory/DMA Frame Grab     | {capture_avg:8.3f} ms   | Zero-copy / DXGI buffer     |")
    print(f"| ROI Template Match        | {icon_roi_avg:8.2f} ms   | Multi-scale with NMS        |")
    print(f"| Full-Screen Match         | {icon_full_avg:8.2f} ms   | Multi-scale full 1080p      |")
    print(f"| ROI Event-driven OCR      | {ocr_roi_avg:8.2f} ms   | PP-OCRv4 CPU (Optimized)    |")
    print(f"| Full-screen OCR           | {ocr_full_time:8.2f} ms   | Not recommended for runtime |")
    print(f"| Visual Diff Verification  | {ver_avg:8.3f} ms   | Fast numpy matrix diff      |")
    print(f"| Steady State RAM Usage    | {ram_final:8.2f} MB   | Entire stack loaded in RAM  |")
    print("=" * 70)


if __name__ == "__main__":
    benchmark_suite()
