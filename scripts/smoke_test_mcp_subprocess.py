"""
scripts/smoke_test_mcp_subprocess.py - Independent Out-of-Process MCP SDK Subprocess Smoke Test

Launches `python -m ugm.server --dry-run` in a completely separate subprocess using the official
`mcp.client.stdio.stdio_client` and `mcp.client.session.ClientSession`.

Verifies:
1. Clean stdio handshake and session initialization.
2. Tool discovery: asserts at least 30 registered UGM tools, including core tools.
3. Positive tool execution contract: `emergency_stop` returns `CallToolResult` with `is_error=False`
   and structured content dictionary.
4. Negative tool execution contract: `hover_target` with invalid argument types returns `CallToolResult`
   with `is_error=True` and descriptive error text in `content`.
5. Exits 0 on success, non-zero on contract violation.
"""

import asyncio
import os
import sys
from typing import Dict, Any


async def run_smoke_test() -> int:
    print("=" * 75)
    print("   UNIVERSAL GAME MCP (UGM) - SUBPROCESS SMOKE TEST")
    print("=" * 75)
    print(f"Python executable: {sys.executable}")
    print("Launching 'ugm.server --dry-run' in independent child subprocess via stdio...")

    try:
        from mcp.client.stdio import StdioServerParameters, stdio_client
        from mcp.client.session import ClientSession
        import mcp.types as types
    except ImportError as e:
        print(f"FATAL: Required MCP SDK client modules not available: {e}", file=sys.stderr)
        return 1

    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "ugm.server", "--dry-run"],
        env={**os.environ}
    )

    try:
        async with stdio_client(server_params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                # 1. Handshake & Initialization
                print("\n[Step 1] Initializing MCP client session...")
                init_result = await session.initialize()
                print(f"  Initialized: protocol_version={init_result.protocol_version}, server_info={init_result.server_info}")

                # 2. Tool Listing Contract
                print("\n[Step 2] Listing registered tools...")
                tool_list = await session.list_tools()
                tools = tool_list.tools
                tool_names = [t.name for t in tools]
                print(f"  Discovered {len(tools)} registered tools.")
                if len(tools) < 30:
                    print(f"FAILURE: Expected at least 30 registered tools, found {len(tools)}: {tool_names}", file=sys.stderr)
                    return 2

                required_core_tools = [
                    "get_game_window",
                    "capture_screen",
                    "read_text",
                    "locate_icon",
                    "hover_target",
                    "click_target",
                    "press_keys",
                    "verify_selection",
                    "emergency_stop",
                    "save_training_sample"
                ]
                missing_core = [t for t in required_core_tools if t not in tool_names]
                if missing_core:
                    print(f"FAILURE: Missing required core tools: {missing_core}", file=sys.stderr)
                    return 3
                print("  All required core tools present.")

                # 3. Positive Tool Call Contract (emergency_stop)
                print("\n[Step 3] Calling 'emergency_stop' (positive check)...")
                res = await session.call_tool("emergency_stop", {"reason": "Subprocess smoke test validation"})
                print(f"  CallToolResult received: is_error={res.is_error}, structured_content type={type(res.structured_content).__name__}")
                if res.is_error is not False:
                    print(f"FAILURE: Expected is_error=False, got {res.is_error}", file=sys.stderr)
                    return 4

                if not isinstance(res.structured_content, dict):
                    print(f"FAILURE: Expected structured_content to be dict, got {type(res.structured_content).__name__}", file=sys.stderr)
                    return 5

                # Extract payload whether wrapped in {"result": ...} or direct
                payload = res.structured_content.get("result", res.structured_content) if isinstance(res.structured_content, dict) else res.structured_content
                if not isinstance(payload, dict) or payload.get("emergency_stopped") is not True:
                    print(f"FAILURE: Unexpected payload structure for emergency_stop: {payload}", file=sys.stderr)
                    return 6
                print(f"  Positive contract verified: emergency_stopped={payload.get('emergency_stopped')}, reason='{payload.get('reason')}'")

                # 4. Negative Tool Call Contract (hover_target with invalid argument types)
                print("\n[Step 4] Calling 'hover_target' with invalid arguments (negative check)...")
                err_res = await session.call_tool("hover_target", {"x": "invalid_non_float", "y": None})
                print(f"  CallToolResult received: is_error={err_res.is_error}, content_blocks={len(err_res.content) if err_res.content else 0}")
                if err_res.is_error is not True:
                    print(f"FAILURE: Expected is_error=True for invalid args, got {err_res.is_error}", file=sys.stderr)
                    return 7

                if not err_res.content or not hasattr(err_res.content[0], "text") or not err_res.content[0].text:
                    print("FAILURE: Expected diagnostic error message in res.content[0].text, but found none", file=sys.stderr)
                    return 8
                print(f"  Negative contract verified: Diagnostic error snippet: {err_res.content[0].text[:120]}...")

                print("\n" + "=" * 75)
                print("   SUBPROCESS SMOKE TEST PASSED WITH ZERO CONTRACT VIOLATIONS")
                print("=" * 75)
                return 0

    except Exception as exc:
        print(f"FATAL: Subprocess smoke test encountered unexpected exception: {exc}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 9


def main() -> None:
    exit_code = asyncio.run(run_smoke_test())
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
