"""
Universal Game MCP (UGM) - Game Skills Manifest & Schema Models
Defines dataclasses and validation logic for versioned game skill packs,
selectors, and skills.
"""

from __future__ import annotations
import json
import os
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional, Any, Tuple
from ..core.types import NormalizedRect


class ManifestValidationError(ValueError):
    """Raised when a Game Skill Pack manifest or component fails validation."""
    pass


@dataclass
class GameMetadata:
    process_names: List[str]
    window_titles: List[str]
    base_resolution: Tuple[int, int]
    supported_resolutions: List[str] = field(default_factory=lambda: ["1920x1080"])
    dpi_aware: bool = True

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> GameMetadata:
        base_res = data.get("base_resolution", [1920, 1080])
        return cls(
            process_names=list(data.get("process_names", [])),
            window_titles=list(data.get("window_titles", [])),
            base_resolution=(int(base_res[0]), int(base_res[1])),
            supported_resolutions=list(data.get("supported_resolutions", ["1920x1080"])),
            dpi_aware=bool(data.get("dpi_aware", True))
        )


@dataclass
class SelectorDefinition:
    selector_id: str
    name: str
    type: str  # "icon", "text", "composite"
    description: str = ""
    search_roi: Optional[NormalizedRect] = None
    template_file: Optional[str] = None
    threshold: float = 0.80
    multi_scale: bool = True
    text_pattern: Optional[str] = None
    case_sensitive: bool = False
    hover_verification: bool = False
    expected_tooltip_contains: Optional[str] = None
    score_tolerance: float = 0.05
    raw_data: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SelectorDefinition:
        sel_id = data.get("selector_id")
        if not sel_id:
            raise ManifestValidationError("Selector definition missing 'selector_id'")

        roi_data = data.get("search_roi")
        search_roi = None
        if roi_data:
            search_roi = NormalizedRect(
                x=float(roi_data["x"]),
                y=float(roi_data["y"]),
                width=float(roi_data["width"]),
                height=float(roi_data["height"])
            )

        tmpl_data = data.get("template") or {}
        text_data = data.get("text_criteria") or {}
        hover_data = data.get("hover_verification") or {}
        ambiguity_data = data.get("ambiguity_handling") or {}

        return cls(
            selector_id=sel_id,
            name=data.get("name", sel_id),
            type=data.get("type", "composite"),
            description=data.get("description", ""),
            search_roi=search_roi,
            template_file=tmpl_data.get("template_file"),
            threshold=float(tmpl_data.get("threshold", 0.80)),
            multi_scale=bool(tmpl_data.get("multi_scale", True)),
            text_pattern=text_data.get("pattern"),
            case_sensitive=bool(text_data.get("case_sensitive", False)),
            hover_verification=bool(hover_data.get("enabled", False)),
            expected_tooltip_contains=hover_data.get("expected_tooltip_contains"),
            score_tolerance=float(ambiguity_data.get("score_tolerance", 0.05)),
            raw_data=data
        )


@dataclass
class SkillDefinition:
    skill_id: str
    version: str
    name: str
    description: str
    preconditions: Dict[str, Any]
    steps: List[Dict[str, Any]]
    post_verification: Dict[str, Any]
    raw_data: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SkillDefinition:
        skill_id = data.get("skill_id")
        if not skill_id:
            raise ManifestValidationError("Skill definition missing 'skill_id'")

        return cls(
            skill_id=skill_id,
            version=data.get("version", "1.0.0"),
            name=data.get("name", skill_id),
            description=data.get("description", ""),
            preconditions=data.get("preconditions", {}),
            steps=data.get("steps", []),
            post_verification=data.get("post_verification", {}),
            raw_data=data
        )


@dataclass
class GameManifest:
    schema_version: str
    game_id: str
    name: str
    version: str
    author: str
    description: str
    game_metadata: GameMetadata
    selectors_path: str = "selectors"
    skills_path: str = "skills"
    templates_path: str = "templates"
    selector_ids: List[str] = field(default_factory=list)
    skill_ids: List[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> GameManifest:
        # Schema validation
        schema_ver = data.get("schema_version")
        if not schema_ver:
            raise ManifestValidationError("Manifest missing 'schema_version'")

        game_id = data.get("game_id")
        if not game_id:
            raise ManifestValidationError("Manifest missing 'game_id'")

        name = data.get("name")
        if not name:
            raise ManifestValidationError("Manifest missing 'name'")

        meta_dict = data.get("game_metadata")
        if not meta_dict:
            raise ManifestValidationError("Manifest missing 'game_metadata'")

        return cls(
            schema_version=str(schema_ver),
            game_id=str(game_id),
            name=str(name),
            version=str(data.get("version", "1.0.0")),
            author=str(data.get("author", "")),
            description=str(data.get("description", "")),
            game_metadata=GameMetadata.from_dict(meta_dict),
            selectors_path=data.get("selectors_path", "selectors"),
            skills_path=data.get("skills_path", "skills"),
            templates_path=data.get("templates_path", "templates"),
            selector_ids=list(data.get("selectors", [])),
            skill_ids=list(data.get("skills", []))
        )
