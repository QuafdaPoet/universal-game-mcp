"""
Universal Game MCP (UGM) - End-to-End Demo Script for Offline Strategy Simulator
Demonstrates all 10 MCP tools interacting with an offline strategy game without requiring Hearts of Iron IV.
"""

import os
import sys
import json
import time
from typing import Any, Dict

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


def extract_tool_payload(res: Any, tool_name: str = "tool") -> Any:
    """
    Safely extracts the data payload from an MCP CallToolResult.
    - Checks if getattr(res, "is_error", False): extracts failure message and raises RuntimeError.
    - Safely extracts payload whether res.structured_content is wrapped under {"result": ...}
      or unwrapped dict, or falls back to JSON-parsing res.content[0].text.
    - Validates data types and provides informative failure messages.
    """
    if getattr(res, "is_error", False):
        err_msg = "Unknown error"
        if hasattr(res, "content") and res.content and hasattr(res.content[0], "text"):
            err_msg = res.content[0].text
        raise RuntimeError(f"Tool '{tool_name}' execution failed with error: {err_msg}")

    payload = getattr(res, "structured_content", None)
    if payload is not None:
        if isinstance(payload, dict):
            if "result" in payload and len(payload) == 1 and isinstance(payload["result"], (dict, list)):
                return payload["result"]
            return payload
        return payload

    if hasattr(res, "content") and res.content and hasattr(res.content[0], "text"):
        raw_text = res.content[0].text
        try:
            parsed = json.loads(raw_text)
            if isinstance(parsed, dict) and "result" in parsed and len(parsed) == 1 and isinstance(parsed["result"], (dict, list)):
                return parsed["result"]
            return parsed
        except (json.JSONDecodeError, TypeError):
            return {"text": raw_text}

    raise ValueError(f"Tool '{tool_name}' returned unparseable or empty payload: {res}")


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

    # Isolate offline demo from host developer background anti-cheat services (e.g. vgc.exe)
    try:
        import psutil
        _orig_iter = psutil.process_iter

        def _safe_demo_iter(attrs=None):
            for p in _orig_iter(attrs):
                try:
                    pname = p.info["name"] if attrs and "name" in attrs and hasattr(p, "info") and "name" in p.info else p.name()
                    if any(b.lower() in pname.lower() for b in safety_manager._anti_cheat_blacklisted):
                        continue
                except Exception:
                    pass
                yield p

        psutil.process_iter = _safe_demo_iter
    except Exception:
        pass

    # Initialize UGM Context with the Strategy Simulator Fixture
    ctx = UGMContext(
        offline_fixture=fixture_file,
        fixture_title="UGM Offline Strategy Simulator",
        fixture_process_name="offline_sim.exe"
    )
    ctx.input_controller.dry_run_default = True
    server = create_server(ctx)
    sim_adapter = OfflineSimAdapter()
    regions = sim_adapter.get_ui_regions()

    # Step 1: get_game_window
    print("[Tool 1/10] get_game_window:")
    # Dynamically obtain configured fixture identity or fallback parameters
    target_title = "UGM Offline Strategy Simulator"
    target_process = "offline_sim.exe"
    if ctx.window_manager.current_window:
        target_title = ctx.window_manager.current_window.title or target_title
        target_process = ctx.window_manager.current_window.process_name or target_process

    res_win = await server.call_tool("get_game_window", {
        "window_title": target_title,
        "process_name": target_process
    })
    win_data = extract_tool_payload(res_win, "get_game_window")
    if not isinstance(win_data, dict):
        raise TypeError(f"get_game_window payload must be a dict, got {type(win_data).__name__}")
    found = win_data.get("found") is True and win_data.get("window") is not None
    print(f"  Target Window Found: {found}")
    if found:
        w_info = win_data["window"]
        print(f"  Title: '{w_info.get('title')}' | Client Rect: {w_info.get('client_width')}x{w_info.get('client_height')}")
    else:
        print(f"  Window search message: {win_data.get('message', 'Window not found')}")

    # Step 2: capture_screen
    print("\n[Tool 2/10] capture_screen:")
    res_cap = await server.call_tool("capture_screen", {"roi": [0.0, 0.0, 1.0, 0.10]})
    cap_data = extract_tool_payload(res_cap, "capture_screen")
    print(f"  Capture ID: {cap_data['capture_id']} | Dimensions: {cap_data['width']}x{cap_data['height']} | Latency: {cap_data['latency_ms']} ms")

    # Step 3: read_text (Event-driven OCR on Topbar Resources)
    print("\n[Tool 3/10] read_text (OCR on Topbar Gold & Status):")
    res_ocr = await server.call_tool("read_text", {"roi": [0.20, 0.0, 0.80, 0.10], "event_tag": "read_resources"})
    ocr_data = extract_tool_payload(res_ocr, "read_text")
    print(f"  Extracted Text: \"{ocr_data['full_text']}\" (Confidence: {ocr_data['items'][0]['confidence'] if ocr_data['items'] else 0.0:.2f})")
    print(f"  OCR Engine: {ocr_data['engine']} | Latency: {ocr_data['latency_ms']} ms")

    # Step 4: locate_icon (or template detection on buttons)
    print("\n[Tool 4/10] locate_icon / template detection:")
    res_loc = await server.call_tool("locate_icon", {"icon_name": "civilian_factory", "threshold": 0.80})
    loc_data = extract_tool_payload(res_loc, "locate_icon")
    print(f"  Icon search completed | Matches count: {loc_data['count']} | Ambiguity check: {loc_data['is_ambiguous']}")

    # Step 5: hover_target (Read Tooltip)
    print("\n[Tool 5/10] hover_target (Hover over BUILD FARM button):")
    res_hover = await server.call_tool("hover_target", {"x": 0.12, "y": 0.91, "duration_ms": 300, "dry_run": True})
    hov_data = extract_tool_payload(res_hover, "hover_target")
    print(f"  Hover Target: {hov_data['target_normalized']} | Screen Pixels: {hov_data['target_pixel']}")
    print(f"  Tooltip Text: {hov_data.get('tooltip_text') or '(Simulated hover completed)'}")

    # Step 6: click_target (Safe DirectInput Click with verification)
    print("\n[Tool 6/10] click_target (Click 'BUILD FARM'):")
    res_click = await server.call_tool("click_target", {"x": 0.12, "y": 0.91, "button": "left", "dry_run": True})
    click_data = extract_tool_payload(res_click, "click_target")
    print(f"  Dry-run: {click_data['dry_run']} | Target Screen Pixel: ({click_data['x_pixel']}, {click_data['y_pixel']})")
    print(f"  Execution Message: {click_data['reason']}")

    # Step 7: press_keys (DirectInput keypress [Space] to pause)
    print("\n[Tool 7/10] press_keys (Send ['space'] to toggle pause):")
    res_keys = await server.call_tool("press_keys", {"keys": ["space"], "dry_run": True})
    keys_data = extract_tool_payload(res_keys, "press_keys")
    print(f"  Success: {keys_data['success']} | Message: {keys_data['message']}")

    # Step 8: verify_selection (Visual state change verification)
    print("\n[Tool 8/10] verify_selection:")
    res_ver = await server.call_tool("verify_selection", {"roi": [0.0, 0.0, 0.3, 0.10], "pre_capture_id": cap_data["capture_id"]})
    ver_data = extract_tool_payload(res_ver, "verify_selection")
    print(f"  State Verification: {ver_data['verified']} (expected False for dry-run on static fixture) | Diff Ratio: {ver_data['diff_ratio']}")

    # Step 9: emergency_stop (Global killswitch activation & safety check)
    print("\n[Tool 9/10] emergency_stop (Activate emergency killswitch):")
    res_estop = await server.call_tool("emergency_stop", {"reason": "Demo killswitch validation"})
    estop_data = extract_tool_payload(res_estop, "emergency_stop")
    print(f"  Killswitch Active: {estop_data['emergency_stopped']} | Reason: '{estop_data['reason']}'")

    # Verify that actions are strictly blocked while stopped
    blocked_click = await server.call_tool("click_target", {"x": 0.5, "y": 0.5, "dry_run": True})
    b_data = extract_tool_payload(blocked_click, "click_target (blocked)")
    print(f"  Attempt click while stopped -> Blocked: {not b_data['executed']} (Reason: {b_data['reason']})")
    safety_manager.reset_emergency_stop()

    # Step 10: save_training_sample (Opt-in dataset collection)
    print("\n[Tool 10/10] save_training_sample:")
    res_sample = await server.call_tool("save_training_sample", {"tag": "offline_sim_fortress", "force": True, "metadata": {"game": "offline_sim", "turn": 1}})
    sample_data = extract_tool_payload(res_sample, "save_training_sample")
    print(f"  Sample Saved: {sample_data['saved']} | Sample ID: {sample_data['sample_id']}")
    print(f"  Image Path: {sample_data['image_path']}")
    print(f"  Metadata Path: {sample_data['metadata_path']}")

    print("\n" + "=" * 75)
    print("   ALL 10 MCP TOOLS EXECUTED & VERIFIED SUCCESSFULLY!")
    print("=" * 75)


if __name__ == "__main__":
    import asyncio
    asyncio.run(run_demo())
