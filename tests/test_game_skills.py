"""
Unit and integration tests for Game Skills Pack loader and manifest validation.
Tests HOI4 reference pack, _template starter pack, schema rules, and V1.5 contracts.
"""

import os
import pytest
from ugm.skills.loader import GameSkillPackLoader, GameSkillPack
from ugm.skills.manifest import GameManifest, ManifestValidationError
from ugm.skills.contracts import (
    SkillExecutionMode,
    NavigationNode,
    NavigationEdge,
    MenuNavigationGraphContract,
    PersistentKnowledgeStorageContract,
    GameSkillsRegistryContract,
    CrossModelSkillShareContract
)


def test_load_hoi4_reference_pack():
    pack_path = os.path.join("game_skills", "hoi4")
    assert os.path.isdir(pack_path), "game_skills/hoi4 directory must exist"

    pack = GameSkillPackLoader.load_pack(pack_path)
    assert isinstance(pack, GameSkillPack)
    assert pack.game_id == "hoi4"
    assert pack.manifest.schema_version == "1.0.0"
    assert pack.manifest.name == "Hearts of Iron IV Reference Pack"

    # Validate declared selectors
    assert len(pack.selectors) >= 5
    assert "topbar_factories" in pack.selectors
    assert "political_power" in pack.selectors
    assert "pause_button" in pack.selectors
    assert "army_group_leader" in pack.selectors
    assert "recruit_tab" in pack.selectors

    # Validate declared skills
    assert len(pack.skills) >= 4
    assert "pause_unpause" in pack.skills
    assert "open_production" in pack.skills
    assert "select_army_group" in pack.skills
    assert "increase_speed" in pack.skills

    # Validate template files exist on disk
    for sel_id, sel in pack.selectors.items():
        if sel.template_file:
            tmpl_abs = pack.get_template_path(sel.template_file)
            assert os.path.isfile(tmpl_abs), f"Referenced template {tmpl_abs} must exist on disk"

    # Validate normalized ROI bounds
    for sel_id, sel in pack.selectors.items():
        if sel.search_roi:
            assert 0.0 <= sel.search_roi.x <= 1.0
            assert 0.0 <= sel.search_roi.y <= 1.0
            assert 0.0 < sel.search_roi.width <= 1.0
            assert 0.0 < sel.search_roi.height <= 1.0


def test_load_template_pack():
    template_path = os.path.join("game_skills", "_template")
    assert os.path.isdir(template_path), "game_skills/_template directory must exist"

    pack = GameSkillPackLoader.load_pack(template_path)
    assert isinstance(pack, GameSkillPack)
    assert pack.game_id == "game_template"
    assert pack.manifest.schema_version == "1.0.0"
    assert "example_selector" in pack.selectors
    assert "example_skill" in pack.skills


def test_manifest_validation_errors(tmp_path):
    # 1. Non-existent directory
    with pytest.raises(ManifestValidationError, match="directory not found"):
        GameSkillPackLoader.load_pack(str(tmp_path / "non_existent"))

    # 2. Directory without manifest.json
    empty_dir = tmp_path / "empty_pack"
    empty_dir.mkdir()
    with pytest.raises(ManifestValidationError, match="Missing manifest.json"):
        GameSkillPackLoader.load_pack(str(empty_dir))

    # 3. Invalid manifest schema (missing game_id)
    bad_manifest = empty_dir / "manifest.json"
    bad_manifest.write_text('{"schema_version": "1.0.0", "name": "Bad Pack"}', encoding="utf-8")
    with pytest.raises(ManifestValidationError, match="missing 'game_id'"):
        GameSkillPackLoader.load_pack(str(empty_dir))


def test_v15_lightweight_contracts():
    # Verify V1.5 contracts and enums are properly defined and importable
    assert SkillExecutionMode.REUSE.value == "reuse"
    assert SkillExecutionMode.EXPLORE.value == "explore"
    assert SkillExecutionMode.TEACH.value == "teach"

    node = NavigationNode(
        node_id="main_menu",
        menu_name="Main Menu",
        game_id="hoi4",
        identification_selector="main_menu_logo"
    )
    assert node.node_id == "main_menu"
    assert node.is_modal is False

    edge = NavigationEdge(
        source_node_id="main_menu",
        target_node_id="single_player",
        trigger_action="click_selector"
    )
    assert edge.source_node_id == "main_menu"
    assert edge.target_node_id == "single_player"
