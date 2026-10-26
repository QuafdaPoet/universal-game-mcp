"""
Unit tests for UGM dataset collection (privacy, opt-in enforcement, and sanitization).
"""

import os
import json
import numpy as np
import pytest

from ugm.core.dataset import DatasetCollector
from ugm.core.types import NormalizedRect


def test_dataset_disabled_by_default(tmp_path):
    storage = str(tmp_path / "samples")
    collector = DatasetCollector(storage_dir=storage, enabled=False)

    dummy_frame = np.zeros((100, 100, 3), dtype=np.uint8)
    res = collector.save_sample(dummy_frame, tag="test_unit")

    assert res["saved"] is False
    assert "disabled by default" in res["reason"]
    assert not os.path.exists(storage)


def test_dataset_save_when_enabled(tmp_path):
    storage = str(tmp_path / "samples")
    collector = DatasetCollector(storage_dir=storage, enabled=True)

    dummy_frame = np.ones((100, 100, 3), dtype=np.uint8) * 128
    meta = {
        "game": "HOI4",
        "api_key": "SECRET_SHOULD_BE_REMOVED",
        "turn": 42
    }

    res = collector.save_sample(
        dummy_frame,
        tag="production_queue",
        roi=NormalizedRect(0.1, 0.1, 0.5, 0.5),
        metadata=meta
    )

    assert res["saved"] is True
    assert os.path.exists(res["image_path"])
    assert os.path.exists(res["metadata_path"])

    # Verify JSON metadata sanitized
    with open(res["metadata_path"], "r", encoding="utf-8") as f:
        data = json.load(f)
    assert data["tag"] == "production_queue"
    assert "api_key" not in data["metadata"]
    assert data["metadata"]["game"] == "HOI4"
    assert data["metadata"]["turn"] == 42
