"""
Universal Game MCP (UGM) - Dataset Collection Subsystem
Manages opt-in saving of training samples and annotations.
Disabled by default to protect user privacy. All stored samples are excluded from Git.
"""

from __future__ import annotations
import os
import time
import json
import uuid
import logging
from typing import Optional, Dict, Any
import numpy as np
import cv2

from .types import NormalizedRect

logger = logging.getLogger("ugm.dataset")


class DatasetCollector:
    """
    Opt-in training data collector.
    Guarantees privacy by being disabled by default and excluding samples from source control.
    """

    def __init__(
        self,
        storage_dir: str = "data/samples",
        enabled: bool = False
    ):
        self.storage_dir = os.path.abspath(storage_dir)
        self.enabled = enabled

    def enable(self) -> None:
        self.enabled = True
        logger.info("Dataset collection ENABLED. Samples will be saved to: %s", self.storage_dir)

    def disable(self) -> None:
        self.enabled = False
        logger.info("Dataset collection DISABLED.")

    def save_sample(
        self,
        image: np.ndarray,
        tag: str,
        roi: Optional[NormalizedRect] = None,
        metadata: Optional[Dict[str, Any]] = None,
        force: bool = False
    ) -> Dict[str, Any]:
        """
        Saves a training sample and JSON metadata if collection is enabled.
        """
        if not self.enabled and not force:
            return {
                "saved": False,
                "reason": "Dataset collection is disabled by default. Set UGM_DATASET_COLLECTION=1 or force=True to opt in.",
                "sample_id": None,
                "path": None,
            }

        os.makedirs(self.storage_dir, exist_ok=True)

        sample_id = f"sample_{int(time.time())}_{uuid.uuid4().hex[:8]}"
        orig_h, orig_w = image.shape[:2]

        crop_img = image
        roi_dict = None

        if roi is not None:
            bbox = roi.to_pixel(orig_w, orig_h)
            crop_img = image[bbox.y:bbox.bottom, bbox.x:bbox.right]
            roi_dict = {
                "x": roi.x,
                "y": roi.y,
                "width": roi.width,
                "height": roi.height,
            }

        img_filename = f"{sample_id}_{tag}.png"
        json_filename = f"{sample_id}_{tag}.json"

        img_path = os.path.join(self.storage_dir, img_filename)
        json_path = os.path.join(self.storage_dir, json_filename)

        # Write image
        cv2.imwrite(img_path, crop_img)

        # Sanitize metadata (remove credentials, system tokens)
        clean_meta = {}
        if metadata:
            for k, v in metadata.items():
                if any(sec in k.lower() for sec in ["key", "pass", "token", "secret", "auth"]):
                    continue
                clean_meta[k] = v

        record = {
            "sample_id": sample_id,
            "tag": tag,
            "timestamp": time.time(),
            "datetime": time.strftime("%Y-%m-%d %H:%M:%S"),
            "image_file": img_filename,
            "dimensions": {"width": crop_img.shape[1], "height": crop_img.shape[0]},
            "roi_normalized": roi_dict,
            "metadata": clean_meta,
        }

        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(record, f, indent=2, ensure_ascii=False)

        logger.info("Saved dataset sample: %s (%s)", sample_id, tag)

        return {
            "saved": True,
            "sample_id": sample_id,
            "tag": tag,
            "image_path": img_path,
            "metadata_path": json_path,
            "dimensions": f"{crop_img.shape[1]}x{crop_img.shape[0]}",
        }
