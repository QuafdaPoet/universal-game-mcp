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

    @staticmethod
    def validate_tag(tag: str) -> str:
        if not tag or not isinstance(tag, str):
            raise ValueError("Dataset sample tag must be a non-empty string.")

        # Sanitize tag: strip navigation characters, slashes, and non-alphanumeric/underscore/hyphen
        sanitized_tag = re.sub(r"[\/\\:]+|\.\.+", "", tag).strip()
        sanitized_tag = re.sub(r"[^a-zA-Z0-9_-]", "", sanitized_tag)

        if not sanitized_tag:
            raise ValueError("Dataset sample tag cannot be empty after sanitization")

        return sanitized_tag

    @staticmethod
    def disabled_result(*, force_allowed: bool) -> Dict[str, Any]:
        opt_in = "Set UGM_DATASET_COLLECTION=1 in the server environment to opt in."
        if force_allowed:
            opt_in += " For an offline fixture, force=True opts in for one sample."
        return {
            "saved": False,
            "reason": f"Dataset collection is disabled by default. {opt_in}",
            "sample_id": None,
            "path": None,
        }

    def save_sample(
        self,
        image: np.ndarray,
        tag: str,
        roi: Optional[NormalizedRect] = None,
        metadata: Optional[Dict[str, Any]] = None,
        force: bool = False,
        *,
        observation_source: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Saves a training sample and JSON metadata if collection is enabled.
        Sanitizes tag to prevent directory traversal and verifies containment within storage_dir.
        """
        sanitized_tag = self.validate_tag(tag)
        force_allowed = observation_source is None or observation_source == "offline_fixture"
        if not self.enabled and not (force and force_allowed):
            return self.disabled_result(force_allowed=force_allowed)

        os.makedirs(self.storage_dir, exist_ok=True)

        sample_id = f"sample_{int(time.time())}_{uuid.uuid4().hex[:8]}"
        orig_h, orig_w = image.shape[:2]

        crop_img = image
        roi_dict = None

        if roi is not None:
            bbox = roi.to_pixel(orig_w, orig_h)
            # A positive normalized rectangle at the edge may round its
            # starting coordinate to exactly the width/height on small frames.
            left, top = min(bbox.x, orig_w - 1), min(bbox.y, orig_h - 1)
            crop_img = image[top:bbox.bottom, left:bbox.right]
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
        if not cv2.imwrite(target_img_path, crop_img):
            raise OSError("Could not write dataset sample image.")

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

        if observation_source is not None:
            record["observation_source"] = observation_source

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
