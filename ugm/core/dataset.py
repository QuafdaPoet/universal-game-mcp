"""
Universal Game MCP (UGM) - Dataset Collection Subsystem
Manages opt-in saving of training samples and annotations.
Disabled by default to protect user privacy. All stored samples are excluded from Git.
"""

from __future__ import annotations
import os
import re
import time
import json
import uuid
import logging
from typing import Optional, Dict, Any
import numpy as np
import cv2

from .types import NormalizedRect
from .safety import SafetyViolationError

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
        Sanitizes tag to prevent directory traversal and verifies containment within storage_dir.
        """
        if not tag or not isinstance(tag, str):
            raise ValueError("Dataset sample tag must be a non-empty string.")

        # Sanitize tag: strip navigation characters, slashes, and non-alphanumeric/underscore/hyphen
        sanitized_tag = re.sub(r"[\/\\:]+|\.\.+", "", tag).strip()
        sanitized_tag = re.sub(r"[^a-zA-Z0-9_-]", "", sanitized_tag)

        if not sanitized_tag:
            raise ValueError("Dataset sample tag cannot be empty after sanitization")

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

        canonical_storage = os.path.realpath(os.path.abspath(self.storage_dir))
        img_filename = f"{sample_id}_{sanitized_tag}.png"
        json_filename = f"{sample_id}_{sanitized_tag}.json"

        target_img_path = os.path.realpath(os.path.abspath(os.path.join(canonical_storage, img_filename)))
        target_json_path = os.path.realpath(os.path.abspath(os.path.join(canonical_storage, json_filename)))

        try:
            common_img = os.path.commonpath([canonical_storage, target_img_path])
            common_json = os.path.commonpath([canonical_storage, target_json_path])
        except ValueError as exc:
            raise SafetyViolationError(f"Path traversal detected in tag '{tag}'") from exc

        if common_img != canonical_storage or common_json != canonical_storage:
            raise SafetyViolationError(f"Path traversal detected in tag '{tag}'")

        # Write image
        cv2.imwrite(target_img_path, crop_img)

        # Sanitize metadata (remove credentials, system tokens)
        clean_meta = {}
        if metadata:
            for k, v in metadata.items():
                if any(sec in k.lower() for sec in ["key", "pass", "token", "secret", "auth"]):
                    continue
                clean_meta[k] = v

        record = {
            "sample_id": sample_id,
            "tag": sanitized_tag,
            "timestamp": time.time(),
            "datetime": time.strftime("%Y-%m-%d %H:%M:%S"),
            "image_file": img_filename,
            "dimensions": {"width": crop_img.shape[1], "height": crop_img.shape[0]},
            "roi_normalized": roi_dict,
            "metadata": clean_meta,
        }

        with open(target_json_path, "w", encoding="utf-8") as f:
            json.dump(record, f, indent=2, ensure_ascii=False)

        logger.info("Saved dataset sample: %s (%s)", sample_id, sanitized_tag)

        return {
            "saved": True,
            "sample_id": sample_id,
            "tag": sanitized_tag,
            "image_path": target_img_path,
            "metadata_path": target_json_path,
            "dimensions": f"{crop_img.shape[1]}x{crop_img.shape[0]}",
        }
