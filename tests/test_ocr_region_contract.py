"""MCP OCR region validation without a screenshot or native input."""

import pytest

from ugm.server import UGMContext, create_server


@pytest.mark.asyncio
@pytest.mark.parametrize("roi", [
    [], [0, 0, .5], [0, 0, .5, .5, .5],
    [-.1, 0, .5, .5], [0, -1, .5, .5], [0, 0, 0, .5], [0, 0, .5, -.1],
    [.9, 0, .2, .5], [0, .9, .5, .2], [float("nan"), 0, .5, .5],
    [0, 0, float("inf"), .5],
])
async def test_bad_ocr_region_is_rejected_before_capture(tmp_path, monkeypatch, roi):
    ctx = UGMContext(db_path=tmp_path / "test.db", ocr_backend="mock", skills_pack_dir=None)
    monkeypatch.setattr(ctx, "capture_observation",
                        lambda: pytest.fail("Invalid OCR region triggered image capture"))
    try:
        with pytest.raises(ValueError, match="OCR ROI"):
            await create_server(ctx).call_tool("read_text", {"roi": roi})
    finally:
        ctx.storage.db.close()
