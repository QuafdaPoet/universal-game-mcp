"""Manually validate window discovery, capture and OCR in an offline Windows game.

This program never requests a hardware action, and launches UGM with --dry-run.
It intentionally requires an explicit process and text expected on the current game screen.
"""
import argparse
import asyncio
import base64
import re
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


def normalize(text):
    return " ".join(text.casefold().split())



def validate_native_triage(result, expected_anchor):
    """Require transient, read-only evidence from a focused native window."""
    labels = result.get("anchors")
    if (result.get("source") != "dry_run_observation" or result.get("samples") != 2
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

async def check(args, db_path):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "ugm.server", "--dry-run", "--db-path", str(db_path)],
    )
    async with asyncio.timeout(90):
        async with stdio_client(params) as streams:
            async with ClientSession(*streams) as session:
                initialization = await session.initialize()

                async def call(tool, arguments):
                    result = await session.call_tool(tool, arguments)
                    if result.is_error or not isinstance(result.structured_content, dict):
                        raise RuntimeError(f"{tool} failed: {result.content}")
                    return result.structured_content.get("result", result.structured_content)

                missing = await call("discover_and_attach_game", {
                    "window_title": "UGM-No-Such-Window-0aef39bf",
                    "process_name": args.process,
                    "auto_detect_foreground": False,
                })
                if missing.get("attached") is not False:
                    raise RuntimeError("An unmatched window was accepted. Stopping before capture.")

                match = await call("discover_and_attach_game", {
                    "window_title": args.title,
                    "process_name": args.process,
                    "auto_detect_foreground": False,
                })
                window = match.get("window") or {}
                if not match.get("attached") or window.get("process_name", "").casefold() != args.process.casefold():
                    raise RuntimeError("Actual game process was not attached. Stopping before capture.")
                if window.get("is_minimized") or not window.get("is_focused"):
                    raise RuntimeError("Game window is not focused. Give it focus during the countdown.")

                image = await call("capture_screen", {})
                if (image.get("width"), image.get("height")) != (window["client_width"], window["client_height"]):
                    raise RuntimeError("Captured dimensions differ from the attached game client.")
                png = base64.b64decode(image["image_base64"], validate=True)
                if not png.startswith(b"\x89PNG\r\n\x1a\n"):
                    raise RuntimeError("No valid PNG returned by capture_screen.")
                ocr = await call("read_text", {})
                if normalize(args.expect_text) not in normalize(ocr.get("full_text", "")):
                    raise RuntimeError(f"Expected text not found; OCR returned {ocr.get('count', 0)} items.")
                triage_note = ""
                if args.expect_anchor:
                    native_triage = await call("triage_ui_state", {})
                    triage_count = validate_native_triage(native_triage, args.expect_anchor)
                    triage_note = f"{triage_count} repeated native OCR anchors; "
                print(f"PASS: UGM {initialization.server_info.version}; "
                      f"{args.process} client {image['width']}x{image['height']}; "
                      f"OCR marker matched; {triage_note}no hardware input requested.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--process", required=True, help="Exact game executable basename, e.g. hoi4.exe")
    parser.add_argument("--title", required=True, help="Literal window title (or stable substring)")
    parser.add_argument("--expect-text", required=True, help="Visible static label, in the game's current language")
    parser.add_argument("--expect-anchor", help="Optionally require a stable label from both read-only triage captures")
    parser.add_argument("--focus-delay", type=int, default=5, choices=range(0, 31), metavar="0..30")
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("This interactive live-game check requires Windows.")
    if not re.fullmatch(r"[^\\/:]+\.exe", args.process, re.IGNORECASE) or not normalize(args.title) or not normalize(args.expect_text):
        parser.error("Supply an executable basename and non-empty title and OCR marker.")

    if args.expect_anchor is not None and not normalize(args.expect_anchor):
        parser.error("--expect-anchor must not be blank.")

    async def run():
        print(f"Switch to the offline game within {args.focus_delay} seconds and leave it focused.", flush=True)
        await asyncio.sleep(args.focus_delay)
        with TemporaryDirectory(prefix="ugm-observe-") as tmp:
            await check(args, Path(tmp) / "observation.db")

    try:
        asyncio.run(run())
    except (Exception, KeyboardInterrupt) as exc:
        print(f"NOT VERIFIED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
