"""
Universal Game MCP (UGM) - Optional Game Asset Indexer
Extracts read-only UI metadata, sprite references, and localization labels
from installed game directories (e.g. Hearts of Iron IV) into a local cache.
Completely read-only; does not modify game files or inject into processes.
"""

from __future__ import annotations
import os
import re
import json
import logging
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict

logger = logging.getLogger("ugm.assets.indexer")


@dataclass
class AssetIndexSummary:
    game_id: str
    game_path: str
    indexed_sprites_count: int
    indexed_windows_count: int
    indexed_localization_count: int
    output_cache_path: str
    timestamp: float


class GameAssetIndexer:
    """
    Safe, read-only game asset parser and indexer for HOI4 and Clausewitz engine titles.
    """
    GAME_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")

    def __init__(self, cache_dir: str = "data/asset_index"):
        self.cache_dir = os.path.abspath(cache_dir)
        os.makedirs(self.cache_dir, exist_ok=True)

    def index_game_directory(
        self,
        game_id: str,
        game_path: str
    ) -> Dict[str, Any]:
        """
        Scans interface/*.gui, interface/*.gfx, and localisation/*.yml in game_path.
        Extracts sprite names, window IDs, and localization entries into local cache.
        """
        if not game_id or not isinstance(game_id, str) or not self.GAME_ID_PATTERN.match(game_id):
            raise ValueError(f"Invalid game_id '{game_id}'. Must match ^[a-zA-Z0-9_-]+$")

        if not os.path.exists(game_path):
            return {
                "success": False,
                "game_id": game_id,
                "game_path": game_path,
                "message": f"Game directory does not exist: {game_path}"
            }

        sprites: Dict[str, str] = {}
        windows: List[str] = []
        loc_keys: Dict[str, str] = {}

        interface_dir = os.path.join(game_path, "interface")
        if os.path.isdir(interface_dir):
            for root, _, files in os.walk(interface_dir):
                for f in files:
                    full_p = os.path.join(root, f)
                    if f.endswith(".gfx"):
                        self._parse_gfx_file(full_p, sprites)
                    elif f.endswith(".gui"):
                        self._parse_gui_file(full_p, windows)

        loc_dir = os.path.join(game_path, "localisation")
        if os.path.isdir(loc_dir):
            for root, _, files in os.walk(loc_dir):
                for f in files:
                    if f.endswith((".yml", ".yaml")):
                        full_p = os.path.join(root, f)
                        self._parse_yml_loc(full_p, loc_keys)

        # Write index summary to disk
        canonical_cache_dir = os.path.realpath(os.path.abspath(self.cache_dir))
        cache_file = os.path.realpath(os.path.abspath(os.path.join(canonical_cache_dir, f"{game_id}_asset_index.json")))
        try:
            common = os.path.commonpath([canonical_cache_dir, cache_file])
        except ValueError as exc:
            raise ValueError(f"Path traversal detected in cache_file for game_id '{game_id}'") from exc

        if common != canonical_cache_dir:
            raise ValueError(f"Path traversal detected in cache_file for game_id '{game_id}'")
        index_data = {
            "game_id": game_id,
            "game_path": os.path.abspath(game_path),
            "sprites_count": len(sprites),
            "windows_count": len(windows),
            "loc_keys_count": len(loc_keys),
            "sample_sprites": list(sprites.keys())[:50],
            "sample_windows": windows[:50],
            "sample_localization": dict(list(loc_keys.items())[:50])
        }

        try:
            with open(cache_file, "w", encoding="utf-8") as fp:
                json.dump(index_data, fp, indent=2)
        except Exception as e:
            logger.warning("Could not persist asset index to %s: %s", cache_file, e)

        return {
            "success": True,
            "game_id": game_id,
            "game_path": game_path,
            "sprites_found": len(sprites),
            "windows_found": len(windows),
            "localization_entries": len(loc_keys),
            "cache_file": cache_file,
            "message": f"Indexed {len(sprites)} sprites, {len(windows)} UI windows, and {len(loc_keys)} localization labels."
        }

    def _parse_gfx_file(self, file_path: str, sprites_out: Dict[str, str]) -> None:
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            # Matches: name = "GFX_..." texturefile = "gfx/..."
            matches = re.findall(
                r'name\s*=\s*["\']([^"\']+)["\']\s+texturefile\s*=\s*["\']([^"\']+)["\']',
                content,
                re.IGNORECASE
            )
            for name, tex in matches:
                sprites_out[name] = tex
        except Exception:
            pass

    def _parse_gui_file(self, file_path: str, windows_out: List[str]) -> None:
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            matches = re.findall(
                r'(?:windowType|containerWindowType)\s*=\s*\{[^}]*?name\s*=\s*["\']([^"\']+)["\']',
                content,
                re.IGNORECASE
            )
            for name in matches:
                if name not in windows_out:
                    windows_out.append(name)
        except Exception:
            pass

    def _parse_yml_loc(self, file_path: str, loc_out: Dict[str, str]) -> None:
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("#") or not line:
                        continue
                    # Matches: KEY:0 "Text string"
                    m = re.match(r'([A-Za-z0-9_\-\.]+):(?:\d+)?\s*"(.*)"', line)
                    if m:
                        loc_out[m.group(1)] = m.group(2)
        except Exception:
            pass
