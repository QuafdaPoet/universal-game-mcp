"""
Universal Game MCP (UGM) - Game Skill Pack Loader
Loads and validates modular Game Skill Packs independently from disk.
"""

from __future__ import annotations
import os
import json
import logging
from typing import Dict, List, Optional
from .manifest import (
    GameManifest,
    SelectorDefinition,
    SkillDefinition,
    ManifestValidationError
)

logger = logging.getLogger("ugm.skills.loader")


class GameSkillPack:
    """Represents a fully loaded and validated Game Skill Pack."""

    def __init__(
        self,
        pack_dir: str,
        manifest: GameManifest,
        selectors: Dict[str, SelectorDefinition],
        skills: Dict[str, SkillDefinition]
    ):
        self.pack_dir = os.path.abspath(pack_dir)
        self.manifest = manifest
        self.selectors = selectors
        self.skills = skills

    @property
    def game_id(self) -> str:
        return self.manifest.game_id

    @property
    def templates_dir(self) -> str:
        return os.path.join(self.pack_dir, self.manifest.templates_path)

    def get_template_path(self, relative_path: str) -> str:
        """Resolves template image path relative to pack directory."""
        return os.path.normpath(os.path.join(self.pack_dir, relative_path))

    def to_dict(self) -> dict:
        return {
            "game_id": self.manifest.game_id,
            "name": self.manifest.name,
            "version": self.manifest.version,
            "selectors_count": len(self.selectors),
            "skills_count": len(self.skills),
            "pack_dir": self.pack_dir
        }


class GameSkillPackLoader:
    """Loads and validates Game Skill Packs."""

    @staticmethod
    def load_pack(pack_dir: str) -> GameSkillPack:
        """Loads and thoroughly validates a Game Skill Pack from directory."""
        if not os.path.isdir(pack_dir):
            raise ManifestValidationError(f"Game skills pack directory not found: {pack_dir}")

        manifest_path = os.path.join(pack_dir, "manifest.json")
        if not os.path.isfile(manifest_path):
            raise ManifestValidationError(f"Missing manifest.json in {pack_dir}")

        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                raw_manifest = json.load(f)
        except Exception as e:
            raise ManifestValidationError(f"Could not parse manifest.json: {e}")

        manifest = GameManifest.from_dict(raw_manifest)

        # Validate selectors directory
        selectors_dir = os.path.join(pack_dir, manifest.selectors_path)
        selectors: Dict[str, SelectorDefinition] = {}

        for sel_id in manifest.selector_ids:
            sel_file = os.path.join(selectors_dir, f"{sel_id}.json")
            if not os.path.isfile(sel_file):
                raise ManifestValidationError(
                    f"Selector '{sel_id}' declared in manifest but file not found: {sel_file}"
                )
            try:
                with open(sel_file, "r", encoding="utf-8") as f:
                    sel_raw = json.load(f)
                sel_def = SelectorDefinition.from_dict(sel_raw)
                # Verify referenced template exists if specified
                if sel_def.template_file:
                    tmpl_abs = os.path.normpath(os.path.join(pack_dir, sel_def.template_file))
                    if not os.path.isfile(tmpl_abs):
                        logger.warning(
                            "Selector '%s' references template '%s' which was not found at %s",
                            sel_id, sel_def.template_file, tmpl_abs
                        )
                selectors[sel_id] = sel_def
            except Exception as e:
                raise ManifestValidationError(f"Failed to load selector '{sel_id}': {e}")

        # Validate skills directory
        skills_dir = os.path.join(pack_dir, manifest.skills_path)
        skills: Dict[str, SkillDefinition] = {}

        for skill_id in manifest.skill_ids:
            skill_file = os.path.join(skills_dir, f"{skill_id}.json")
            if not os.path.isfile(skill_file):
                raise ManifestValidationError(
                    f"Skill '{skill_id}' declared in manifest but file not found: {skill_file}"
                )
            try:
                with open(skill_file, "r", encoding="utf-8") as f:
                    skill_raw = json.load(f)
                skill_def = SkillDefinition.from_dict(skill_raw)
                skills[skill_id] = skill_def
            except Exception as e:
                raise ManifestValidationError(f"Failed to load skill '{skill_id}': {e}")

        logger.info(
            "Successfully loaded and validated Game Skill Pack '%s' (%s): %d selectors, %d skills",
            manifest.name, manifest.game_id, len(selectors), len(skills)
        )

        return GameSkillPack(
            pack_dir=pack_dir,
            manifest=manifest,
            selectors=selectors,
            skills=skills
        )
