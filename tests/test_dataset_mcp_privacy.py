"""Dataset collection cannot capture real frames without operator consent."""

import json

import cv2
import pytest

from ugm.core.safety import SafetyViolationError
from ugm.server import UGMContext, create_server


@pytest.fixture
def new_context(tmp_path, monkeypatch):
    monkeypatch.delenv("UGM_DATASET_COLLECTION", raising=False)
    contexts = []

    def make(**kwargs):
        ctx = UGMContext(db_path=tmp_path / "test.db", ocr_backend="mock",
                         skills_pack_dir=None, **kwargs)
        contexts.append(ctx)
        ctx.dataset_collector.storage_dir = str(tmp_path / "samples")
        return ctx

    yield make
    for ctx in contexts:
        ctx.storage.db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("force", [False, True])
async def test_native_disabled_never_captures(new_context, tmp_path, monkeypatch, force):
    ctx = new_context()
    def forbidden(*args, **kwargs):
        pytest.fail("Native dataset opt-out attempted capture")
    monkeypatch.setattr(ctx.capture_manager, "capture", forbidden)
    monkeypatch.setattr(ctx.capture_manager, "capture_attached_window", forbidden)
    res = await create_server(ctx).call_tool("save_training_sample", {"tag": "test", "force": force})
    assert not res.is_error
    data = res.structured_content.get("result", res.structured_content)
    assert data["saved"] is False
    assert not (tmp_path / "samples").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("args", [
    {"tag": "test", "roi": []},
    {"tag": "test", "roi": [.9, 0, .2, .5]},
    {"tag": "test", "roi": [0, 0, float("nan"), .5]},
    {"tag": "../../"},
])
async def test_invalid_sample_rejected_before_capture(new_context, tmp_path, monkeypatch, args):
    monkeypatch.setenv("UGM_DATASET_COLLECTION", "1")
    ctx = new_context()
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid dataset request attempted capture")
    monkeypatch.setattr(ctx.capture_manager, "capture", forbidden)
    monkeypatch.setattr(ctx.capture_manager, "capture_attached_window", forbidden)
    with pytest.raises(ValueError, match="Dataset"):
        await create_server(ctx).call_tool("save_training_sample", args)
    assert not (tmp_path / "samples").exists()


@pytest.mark.asyncio
async def test_enabled_native_without_attachment_fails_closed(new_context, tmp_path):
    ctx = new_context()
    ctx.dataset_collector.enable()
    with pytest.raises(SafetyViolationError):
        await create_server(ctx).call_tool("save_training_sample", {"tag": "test"})
    assert not (tmp_path / "samples").exists()


@pytest.mark.asyncio
async def test_fixture_force_is_cropped_and_labeled_by_server(new_context):
    ctx = new_context(offline_fixture="tests/fixtures/hoi4_topbar.png")
    res = await create_server(ctx).call_tool("save_training_sample", {
        "tag": "test", "force": True, "roi": [0, 0, .5, .5],
        "metadata": {"evidence": "verified"},
    })
    assert not res.is_error
    data = res.structured_content.get("result", res.structured_content)
    with open(data["metadata_path"], encoding="utf-8") as handle:
        record = json.load(handle)
    assert record["observation_source"] == "offline_fixture"
    expected = cv2.imread("tests/fixtures/hoi4_topbar.png")
    saved = cv2.imread(data["image_path"])
    assert (saved == expected[:round(expected.shape[0] / 2), :round(expected.shape[1] / 2)]).all()


@pytest.mark.asyncio
@pytest.mark.parametrize(("dry_run", "source"), [
    (False, "live_observation"),
    (True, "dry_run_observation"),
])
async def test_enabled_native_uses_scoped_frame_and_records_source(
    new_context, monkeypatch, dry_run, source,
):
    import numpy as np

    monkeypatch.setenv("UGM_DATASET_COLLECTION", "1")
    ctx = new_context(enforce_dry_run=dry_run)
    assert ctx.dataset_collector.enabled
    frame = np.full((8, 8, 3), 42, dtype=np.uint8)
    verified_calls = []
    def scoped(window_manager, expected):
        assert window_manager is ctx.window_manager
        verified_calls.append(expected)
        return frame
    def forbidden(*args, **kwargs):
        pytest.fail("Used unscoped capture for a native dataset sample")
    monkeypatch.setattr(ctx.capture_manager, "capture_attached_window", scoped)
    monkeypatch.setattr(ctx.capture_manager, "capture", forbidden)
    res = await create_server(ctx).call_tool("save_training_sample", {"tag": "test"})
    assert not res.is_error
    data = res.structured_content.get("result", res.structured_content)
    assert len(verified_calls) == 1
    with open(data["metadata_path"], encoding="utf-8") as handle:
        assert json.load(handle)["observation_source"] == source
    assert (cv2.imread(data["image_path"]) == frame).all()
