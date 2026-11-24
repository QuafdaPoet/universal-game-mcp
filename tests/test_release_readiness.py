"""Release smoke checks that do not require a game or physical inputs."""

from pathlib import Path
import subprocess
import sys

import pytest

from ugm.core.capture import CaptureManager, FixtureBackend
from ugm.server import UGMContext

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "sim_fixture.png"


def test_fixture_starts_outside_checkout_without_live_capture(monkeypatch, tmp_path):
    def refuse_live_backend(_self):
        pytest.fail("fixture mode initialized a live capture backend")

    monkeypatch.setattr(CaptureManager, "_init_preferred_backend", refuse_live_backend)
    monkeypatch.chdir(tmp_path)
    ctx = UGMContext(offline_fixture=str(FIXTURE), db_path=tmp_path / "knowledge.db")
    assert isinstance(ctx.capture_manager.backend, FixtureBackend)
    assert ctx.input_controller.is_dry_run_active(False)
    pack = ctx.skill_packs["hoi4"]
    assert pack.skills and pack.selectors
    assert (Path(pack.templates_dir) / "pause_btn.png").is_file()


def test_missing_fixture_fails_before_startup(tmp_path):
    proc = subprocess.run(
        [sys.executable, "-m", "ugm.server", "--fixture", str(tmp_path / "missing.png"),
         "--db-path", str(tmp_path / "knowledge.db")],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 2
    assert "existing image file" in proc.stderr
    assert not (tmp_path / "knowledge.db").exists()


@pytest.mark.asyncio
async def test_text_verification_and_cropped_baseline(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import numpy as np

    from ugm.server import create_server

    ctx = UGMContext(offline_fixture=str(FIXTURE), db_path=tmp_path / "knowledge.db")
    server = create_server(ctx)
    monkeypatch.setattr(ctx.ocr_engine, "read_text",
                        lambda *args, **kwargs: SimpleNamespace(full_text="Research"))
    backend = ctx.capture_manager.backend
    original = np.zeros((64, 64, 3), dtype=np.uint8)
    backend.set_frame(original)

    async def call(name, args):
        result = await server.call_tool(name, args)
        assert not result.is_error
        return result.structured_content.get("result", result.structured_content)

    assert (await call("verify_state", {"expected_text": "Research"}))["verified"]
    assert not (await call("verify_state", {"expected_text": "Settings"}))["verified"]

    roi = [0.5, 0.5, 0.5, 0.5]
    capture = await call("capture_screen", {"roi": roi})
    assert (capture["width"], capture["height"]) == (32, 32)
    changed = original.copy()
    changed[32:, 32:] = 255
    backend.set_frame(changed)
    result = await call("verify_state", {"roi": roi, "pre_capture_id": capture["capture_id"]})
    assert result["verified"] and result["diff_ratio"] == 1
