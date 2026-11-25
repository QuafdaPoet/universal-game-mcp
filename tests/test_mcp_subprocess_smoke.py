"""
tests/test_mcp_subprocess_smoke.py - Pytest Regression Test for MCP SDK Out-of-Process Subprocess

Runs scripts/smoke_test_mcp_subprocess.py in an isolated child process to ensure full
protocol compliance with the installed MCP SDK v2 across stdio transport.
"""

import os
import sys
import subprocess
import pytest


def test_mcp_subprocess_smoke():
    """
    Executes the standalone MCP subprocess smoke test script.
    Asserts returncode == 0, verifying tool discovery, positive tool execution,
    negative error propagation, and stdio handshake compliance.
    """
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    smoke_script = os.path.join(repo_root, "scripts", "smoke_test_mcp_subprocess.py")
    assert os.path.isfile(smoke_script), f"Smoke test script not found at {smoke_script}"

    proc = subprocess.run(
        [sys.executable, smoke_script],
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=60
    )

    print("STDOUT:\n", proc.stdout)
    if proc.stderr:
        print("STDERR:\n", proc.stderr)

    assert proc.returncode == 0, (
        f"Subprocess smoke test failed with returncode {proc.returncode}.\n"
        f"STDOUT: {proc.stdout}\n"
        f"STDERR: {proc.stderr}"
    )
