"""Manually validate window discovery, capture and OCR in an offline Windows game.

No game packs, no hardware input, and no game-state or save-file writes.
The optional native evidence check writes only to an isolated temporary UGM database.
"""
import argparse
import asyncio
import base64
import json
import platform
import re
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import psutil


def normalize(text):
    return " ".join(text.casefold().split())



def validate_native_triage(result, expected_anchor, *, source="dry_run_observation"):
    """Require transient, read-only evidence from a focused native window."""
    labels = result.get("anchors")
    if (result.get("source") != source or result.get("samples") != 2
            or result.get("state_name") != "unknown" or result.get("status") != "stable_candidate"
            or result.get("action_suggested") is not False
            or any(k in result for k in ("route", "coordinates", "destinations", "action"))
            or not isinstance(labels, list) or not 2 <= len(labels) <= 6
            or not all(isinstance(label, str) and normalize(label) for label in labels)):
        raise RuntimeError("Native read-only triage did not produce two stable observations.")
    unique = {normalize(label) for label in labels}
    if len(unique) != len(labels) or normalize(expected_anchor) not in unique:
        raise RuntimeError("Expected persistent UI anchor was missing or ambiguous.")
    return len(labels)

SIGNATURE = r"provisional_[a-f0-9]{32}"
OBSERVATION_TOOLS = frozenset({
    "discover_and_attach_game", "capture_screen", "read_text", "triage_ui_state",
    "identify_provisional_ui", "observe_ui_selector",
})
RAM_BUDGET_BYTES = 250_000_000


def validate_provisional_selector(result, identity):
    evidence = result.get("evidence") or {}
    if (result.get("provisional_id") != identity or result.get("state_name") != "unknown"
            or result.get("match") != "found" or result.get("bbox_normalized", True) is not None
            or result.get("action_suggested") is not False or result.get("interactivity_verified") is not False
            or evidence.get("status") not in {"candidate", "repeat_observed"}
            or not isinstance(evidence.get("observations"), int) or evidence["observations"] < 1):
        raise RuntimeError("Provisional selector did not produce isolated label evidence.")


async def check_provisional_learning(call, reattach, label, *, sample_interval=.4):
    """Bounded attempt; identical pixels, ambiguity or moving anchors may abstain."""
    identity = None
    result = None
    for attachment in range(2):
        if attachment:
            await reattach()
        for _ in range(3):
            result = await call("identify_provisional_ui", {"record": True})
            candidate = result.get("provisional_id")
            if (result.get("state_name") != "unknown" or result.get("action_suggested") is not False
                    or result.get("status") not in {"provisional_candidate", "provisional_match"}
                    or not isinstance(candidate, str) or not re.fullmatch(SIGNATURE, candidate)
                    or (identity is not None and candidate != identity)):
                raise RuntimeError("The game did not yield one stable anonymous screen.")
            identity = candidate
            if result.get("status") == "provisional_match":
                break
            await asyncio.sleep(sample_interval)
    if not result or result.get("status") != "provisional_match":
        raise RuntimeError("Insufficient distinct frame evidence across both attachments; do not force a match.")
    count = result.get("receipt_count")
    again = await call("identify_provisional_ui", {"record": False})
    if (not isinstance(count, int) or count < 3 or again.get("receipt_count") != count
            or again.get("status") != "provisional_match" or again.get("provisional_id") != identity
            or again.get("state_name") != "unknown" or again.get("action_suggested") is not False):
        raise RuntimeError("Fresh read-only provisional lookup did not match the recorded screen.")
    selector = await call("observe_ui_selector", {
        "selector_id": "acceptance", "label": label, "provisional_id": identity, "record": True,
    })
    validate_provisional_selector(selector, identity)
    if selector["evidence"].get("new_receipt") is not True:
        raise RuntimeError("The temporary selector ledger did not record a fresh receipt.")
    return count


async def check_observations(args, call, report):
    stage = report["stages"]
    def started(name):
        stage[name] = "not_verified"
    def passed(name):
        stage[name] = "pass"

    async def attach(title):
        return await call("discover_and_attach_game", {
            "window_title": title, "process_name": args.process, "auto_detect_foreground": False,
        })

    started("nonexistent_window_rejected")
    missing = await attach("UGM-No-Such-Window-0aef39bf")
    if missing.get("attached") is not False:
        raise RuntimeError("An unmatched window was accepted. Stopping before capture.")
    passed("nonexistent_window_rejected")

    started("focused_game_attachment")
    async def verified_attach(stage_name="focused_game_attachment"):
        match = await attach(args.title)
        window = match.get("window") or {}
        if (match.get("attached") is not True
                or window.get("process_name", "").casefold() != args.process.casefold()
                or any(type(window.get(key)) is not int or window[key] <= 0
                       for key in ("hwnd", "pid", "client_width", "client_height"))):
            stage[stage_name] = "blocked"
            raise RuntimeError("Actual game process was not attached. Stopping before capture.")
        if window.get("is_minimized") or window.get("is_focused") is not True:
            stage[stage_name] = "blocked"
            raise RuntimeError("Game window is not focused. Give it focus during the countdown.")
        return window
    window = await verified_attach()
    passed("focused_game_attachment")

    started("native_capture")
    image = await call("capture_screen", {})
    size = (image.get("width"), image.get("height"))
    if size != (window["client_width"], window["client_height"]):
        raise RuntimeError("Captured dimensions differ from the attached game client.")
    png = base64.b64decode(image["image_base64"], validate=True)
    if not png.startswith(b"\x89PNG\r\n\x1a\n"):
        raise RuntimeError("No valid PNG returned by capture_screen.")
    report["client_size"] = list(size)
    del png, image
    passed("native_capture")

    started("native_ocr_marker")
    ocr = await call("read_text", {})
    if normalize(args.expect_text) not in normalize(ocr.get("full_text", "")):
        raise RuntimeError(f"Expected text not found; OCR returned {ocr.get('count', 0)} items.")
    passed("native_ocr_marker")

    if args.expect_anchor:
        started("stable_unknown_triage")
        result = await call("triage_ui_state", {})
        source = "live_observation" if args.learn_provisional else "dry_run_observation"
        report["triage_anchor_count"] = validate_native_triage(result, args.expect_anchor, source=source)
        passed("stable_unknown_triage")

    if args.learn_provisional:
        started("provisional_screen_and_label")
        async def reattach():
            refreshed = await verified_attach("provisional_screen_and_label")
            if any(refreshed.get(key) != window.get(key) for key in ("hwnd", "pid", "client_width", "client_height")):
                raise RuntimeError("The second attachment was not the same game window.")

        report["anonymous_screen_receipts"] = await check_provisional_learning(call, reattach, args.expect_anchor)
        passed("provisional_screen_and_label")


def forbid_input(*_args, **_kwargs):
    raise RuntimeError("Hardware input is disabled by the acceptance harness.")


async def check(args, db_path, report):
    # No exposed stdio/network server. Only the six allowlisted MCP tools can
    # be called by this script. Guard hardware methods independently of the
    # dry-run setting: provisional observations require the native namespace.
    from ugm import __version__
    from ugm.server import UGMContext, create_server

    ctx = UGMContext(db_path=db_path, skills_pack_dir=None, enforce_dry_run=not args.learn_provisional)
    ctx.dataset_collector.enabled = False
    ctx._provisional_ui_learning = args.learn_provisional
    ctx._semantic_selector_learning = args.learn_provisional
    for method in ("click", "hover", "press_keys"):
        setattr(ctx.input_controller, method, forbid_input)
    report["ugm_version"] = __version__
    report["skill_packs_loaded"] = len(ctx.skill_packs)
    try:
        if ctx.skill_packs:
            raise RuntimeError("This acceptance harness must start without a game pack.")
        server = create_server(ctx)

        async def call(tool, arguments):
            if tool not in OBSERVATION_TOOLS:
                raise RuntimeError("Tool is outside the observation allowlist.")
            result = await server.call_tool(tool, arguments)
            if result.is_error or not isinstance(result.structured_content, dict):
                raise RuntimeError(f"Observation tool failed: {tool}.")
            return result.structured_content.get("result", result.structured_content)

        async with asyncio.timeout(90):
            await check_observations(args, call, report)
    finally:
        ctx.storage.db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--process", required=True, help="Exact game executable basename, e.g. hoi4.exe")
    parser.add_argument("--title", required=True, help="Literal window title (or stable substring)")
    parser.add_argument("--expect-text", required=True, help="Visible static label, in the game's current language")
    parser.add_argument("--expect-anchor", help="Optionally require a stable label from both read-only triage captures")
    parser.add_argument("--learn-provisional", action="store_true",
                        help="Try repeat native evidence in a temporary DB; requires --expect-anchor")
    parser.add_argument("--game-version", help="Operator-reported version identifier")
    parser.add_argument("--window-mode", choices=("windowed", "borderless"))
    parser.add_argument("--ui-scale", help="Operator-reported scale, e.g. 100%%")
    parser.add_argument("--focus-delay", type=int, default=5, choices=range(0, 31), metavar="0..30")
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("This interactive live-game check requires Windows.")
    if not re.fullmatch(r"[^\\/:]+\.exe", args.process, re.IGNORECASE) or not normalize(args.title) or not normalize(args.expect_text):
        parser.error("Supply an executable basename and non-empty title and OCR marker.")

    if args.expect_anchor is not None and not normalize(args.expect_anchor):
        parser.error("--expect-anchor must not be blank.")

    if args.learn_provisional and not args.expect_anchor:
        parser.error("--learn-provisional requires --expect-anchor.")

    report = {"evidence": "native_windows_observation_only", "process": args.process,
              "windows": platform.version(), "game_version": args.game_version,
              "window_mode": args.window_mode, "ui_scale": args.ui_scale,
              "hardware_input_requested": False,
              "stages": dict.fromkeys(("nonexistent_window_rejected", "focused_game_attachment", "native_capture",
                                       "native_ocr_marker", "stable_unknown_triage", "provisional_screen_and_label",
                                       "gameplay"), "not_run")}

    async def run():
        print(f"Switch to the offline game within {args.focus_delay} seconds and leave it focused.", flush=True)
        await asyncio.sleep(args.focus_delay)
        with TemporaryDirectory(prefix="ugm-observe-") as tmp:
            await check(args, Path(tmp) / "observation.db", report)

    status = 0
    try:
        asyncio.run(run())
    except (Exception, KeyboardInterrupt) as exc:
        print(f"NOT VERIFIED: {type(exc).__name__}: {exc}", file=sys.stderr)
        status = 1
    else:
        print("PASS: requested native observation checks; no hardware input requested.")
    peak = psutil.Process().memory_info().peak_wset
    report["peak_working_set_bytes"] = peak
    report["ram_gate_250mb"] = ("not_verified" if status else
                                "pass_for_this_observation" if 0 < peak <= RAM_BUDGET_BYTES else "above_budget")
    print(json.dumps(report, sort_keys=True))
    return status


if __name__ == "__main__":
    sys.exit(main())
