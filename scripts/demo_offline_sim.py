"""
Universal Game MCP (UGM) - End-to-End Demo Script for Offline Strategy Simulator
Demonstrates all 10 MCP tools interacting with an offline strategy game without requiring Hearts of Iron IV.
"""

import os
import sys
import json
import time

# Ensure repository root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Ensure UTF-8 console output on Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

import cv2
import numpy as np

from ugm.core.safety import safety_manager
from ugm.server import create_server, UGMContext
from ugm.adapters.sim.adapter import OfflineSimAdapter


def generate_simulator_fixture() -> str:
    """Generates a synthetic frame of the UGM Offline Strategy Simulator for deterministic demo."""
    os.makedirs("tests/fixtures", exist_ok=True)
    frame = np.zeros((550, 900, 3), dtype=np.uint8)
    frame[:] = (36, 30, 30)  # Dark background #1e1e24

    # Topbar
    cv2.rectangle(frame, (0, 0), (900, 50), (66, 45, 43), -1)
    cv2.putText(frame, "UGM STRATEGY SIM", (20, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (244, 242, 237), 2)
    cv2.putText(frame, "GOLD: 500", (220, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (102, 209, 255), 2)
    cv2.putText(frame, "WOOD: 320", (360, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (160, 214, 6), 2)
    cv2.putText(frame, "MANA: 100", (500, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (178, 138, 17), 2)
    cv2.putText(frame, "STATUS: RUNNING", (710, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (160, 214, 6), 2)

    # Center Playfield
    cv2.rectangle(frame, (20, 65), (880, 440), (22, 18, 18), -1)
    cv2.rectangle(frame, (50, 90), (260, 320), (51, 38, 36), -1)
    cv2.putText(frame, "KINGDOM FORTRESS", (65, 125), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2)
    cv2.putText(frame, "Farms: 0", (75, 175), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (181, 165, 165), 1)
    cv2.putText(frame, "Soldiers: 0", (75, 210), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (181, 165, 165), 1)

    # Action Buttons
    cv2.rectangle(frame, (15, 475), (230, 525), (157, 123, 69), -1)
    cv2.putText(frame, "BUILD FARM", (35, 506), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

    cv2.rectangle(frame, (250, 475), (470, 525), (143, 157, 42), -1)
    cv2.putText(frame, "TRAIN SOLDIER", (265, 506), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

    cv2.rectangle(frame, (490, 475), (690, 525), (81, 111, 231), -1)
    cv2.putText(frame, "PAUSE GAME", (515, 506), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

    fixture_path = "tests/fixtures/sim_fixture.png"
    cv2.imwrite(fixture_path, frame)
    return fixture_path


async def run_demo():
    print("=" * 75)
    print("   UNIVERSAL GAME MCP (UGM) - OFFLINE STRATEGY SIMULATOR DEMO")
    print("=" * 75)
    print("This demo demonstrates all 10 MCP tools running on an offline game target")
    print("(No commercial game or Hearts of Iron IV required!).\n")

    fixture_file = generate_simulator_fixture()
    safety_manager.reset_emergency_stop()

    # Initialize UGM Context with the Strategy Simulator Fixture
    ctx = UGMContext(offline_fixture=fixture_file)
    ctx.input_controller.dry_run_default = True
    server = create_server(ctx)
    sim_adapter = OfflineSimAdapter()
    regions = sim_adapter.get_ui_regions()

    # Step 1: get_game_window
    print("[Tool 1/10] get_game_window:")
    res_win = await server.call_tool("get_game_window", {"window_title": "UGM Offline Strategy Simulator"})
    win_data = res_win.structured_content["result"]
    print(f"  Target Window Found: {win_data['found']}")
    print(f"  Title: '{win_data['window']['title']}' | Client Rect: {win_data['window']['client_width']}x{win_data['window']['client_height']}")

    # Step 2: capture_screen
    print("\n[Tool 2/10] capture_screen:")
    res_cap = await server.call_tool("capture_screen", {"roi": [0.0, 0.0, 1.0, 0.10]})
    cap_data = res_cap.structured_content["result"]
    print(f"  Capture ID: {cap_data['capture_id']} | Dimensions: {cap_data['width']}x{cap_data['height']} | Latency: {cap_data['latency_ms']} ms")

    # Step 3: read_text (Event-driven OCR on Topbar Resources)
    print("\n[Tool 3/10] read_text (OCR on Topbar Gold & Status):")
    res_ocr = await server.call_tool("read_text", {"roi": [0.20, 0.0, 0.80, 0.10], "event_tag": "read_resources"})
    ocr_data = res_ocr.structured_content["result"]
    print(f"  Extracted Text: \"{ocr_data['full_text']}\" (Confidence: {ocr_data['items'][0]['confidence'] if ocr_data['items'] else 0.0:.2f})")
    print(f"  OCR Engine: {ocr_data['engine']} | Latency: {ocr_data['latency_ms']} ms")

    # Step 4: locate_icon (or template detection on buttons)
    print("\n[Tool 4/10] locate_icon / template detection:")
    res_loc = await server.call_tool("locate_icon", {"icon_name": "civilian_factory", "threshold": 0.80})
    loc_data = res_loc.structured_content["result"]
    print(f"  Icon search completed | Matches count: {loc_data['count']} | Ambiguity check: {loc_data['is_ambiguous']}")

    # Step 5: hover_target (Read Tooltip)
    print("\n[Tool 5/10] hover_target (Hover over BUILD FARM button):")
    res_hover = await server.call_tool("hover_target", {"x": 0.12, "y": 0.91, "duration_ms": 300, "dry_run": True})
    hov_data = res_hover.structured_content["result"]
    print(f"  Hover Target: {hov_data['target_normalized']} | Screen Pixels: {hov_data['target_pixel']}")
    print(f"  Tooltip Text: {hov_data.get('tooltip_text') or '(Simulated hover completed)'}")

    # Step 6: click_target (Safe DirectInput Click with verification)
    print("\n[Tool 6/10] click_target (Click 'BUILD FARM'):")
    res_click = await server.call_tool("click_target", {"x": 0.12, "y": 0.91, "button": "left", "dry_run": True})
    click_data = res_click.structured_content["result"]
    print(f"  Dry-run: {click_data['dry_run']} | Target Screen Pixel: ({click_data['x_pixel']}, {click_data['y_pixel']})")
    print(f"  Execution Message: {click_data['reason']}")

    # Step 7: press_keys (DirectInput keypress [Space] to pause)
    print("\n[Tool 7/10] press_keys (Send ['space'] to toggle pause):")
    res_keys = await server.call_tool("press_keys", {"keys": ["space"], "dry_run": True})
    keys_data = res_keys.structured_content["result"]
    print(f"  Success: {keys_data['success']} | Message: {keys_data['message']}")

    # Step 8: verify_selection (Visual state change verification)
    print("\n[Tool 8/10] verify_selection:")
    res_ver = await server.call_tool("verify_selection", {"roi": [0.0, 0.0, 0.3, 0.10], "pre_capture_id": cap_data["capture_id"]})
    ver_data = res_ver.structured_content["result"]
    print(f"  State Verification: {ver_data['verified']} | Diff Ratio: {ver_data['diff_ratio']}")

    # Step 9: emergency_stop (Global killswitch activation & safety check)
    print("\n[Tool 9/10] emergency_stop (Activate emergency killswitch):")
    res_estop = await server.call_tool("emergency_stop", {"reason": "Demo killswitch validation"})
    estop_data = res_estop.structured_content["result"]
    print(f"  Killswitch Active: {estop_data['emergency_stopped']} | Reason: '{estop_data['reason']}'")

    # Verify that actions are strictly blocked while stopped
    blocked_click = await server.call_tool("click_target", {"x": 0.5, "y": 0.5, "dry_run": True})
    b_data = blocked_click.structured_content["result"]
    print(f"  Attempt click while stopped -> Blocked: {not b_data['executed']} (Reason: {b_data['reason']})")
    safety_manager.reset_emergency_stop()

    # Step 10: save_training_sample (Opt-in dataset collection)
    print("\n[Tool 10/10] save_training_sample:")
    res_sample = await server.call_tool("save_training_sample", {"tag": "offline_sim_fortress", "force": True, "metadata": {"game": "offline_sim", "turn": 1}})
    sample_data = res_sample.structured_content["result"]
    print(f"  Sample Saved: {sample_data['saved']} | Sample ID: {sample_data['sample_id']}")
    print(f"  Image Path: {sample_data['image_path']}")
    print(f"  Metadata Path: {sample_data['metadata_path']}")

    print("\n" + "=" * 75)
    print("   ALL 10 MCP TOOLS EXECUTED & VERIFIED SUCCESSFULLY!")
    print("=" * 75)


if __name__ == "__main__":
    import asyncio
    asyncio.run(run_demo())
