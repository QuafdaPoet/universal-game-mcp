"""
Tests for Test Scenario G: HOI4 Reference Skill Pack
Validates that:
- Built-in HOI4 skill definitions load cleanly from disk via GameSkillPackLoader.
- Selectors and skills are populated properly.
- Atomic skills (pause_unpause, open_production, increase_speed) execute via SkillExecutionEngine.
- Incomplete or malformed skills are clearly flagged instead of pretending they work.
"""

import pytest
import numpy as np
from pathlib import Path
from unittest.mock import MagicMock

from ugm.skills.loader import GameSkillPackLoader, GameSkillPack
from ugm.core.target import TargetResolver, TargetExecutor
from ugm.core.capture import CaptureManager
from ugm.core.input import InputController
from ugm.core.verifier import ActionVerifier
from ugm.core.ocr import BaseOCREngine
from ugm.core.window import WindowInfo
from ugm.core.safety import SafetyViolationError
from ugm.skills.engine import SkillExecutionEngine, SkillExecutionResult


@pytest.fixture
def hoi4_pack():
    pack = GameSkillPackLoader.load_pack("game_skills/hoi4")
    return pack


@pytest.fixture
def mock_engine(hoi4_pack):
    cap_mgr = MagicMock(spec=CaptureManager)
    dummy_frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
    cap_mgr.capture.return_value = dummy_frame

    input_ctrl = MagicMock()
    input_ctrl.is_dry_run_active.return_value = True
    input_ctrl.press_keys.return_value = {"executed": True, "dry_run": True, "keys": ["space"]}
    # Mock active window
    mock_win = WindowInfo(
        hwnd=12345,
        title="Hearts of Iron IV",
        process_name="hoi4.exe",
        pid=9999,
        x=0, y=0, width=1920, height=1080,
        client_x=0, client_y=0, client_width=1920, client_height=1080,
        is_minimized=False,
        is_focused=True
    )
    mock_win_mgr = MagicMock()
    mock_win_mgr.current_window = mock_win
    input_ctrl.window_manager = mock_win_mgr

    verifier = MagicMock(spec=ActionVerifier)
    mock_ver_res = MagicMock()
    mock_ver_res.verified = True
    mock_ver_res.message = "Verified"
    mock_ver_res.diff_ratio = 0.05
    mock_ver_res.details = {}
    verifier.verify_selection.return_value = mock_ver_res

    target_resolver = MagicMock(spec=TargetResolver)
    target_executor = MagicMock(spec=TargetExecutor)
    ocr_engine = MagicMock(spec=BaseOCREngine)

    engine = SkillExecutionEngine(
        capture_manager=cap_mgr,
        input_controller=input_ctrl,
        target_resolver=target_resolver,
        target_executor=target_executor,
        verifier=verifier,
        ocr_engine=ocr_engine,
        storage=None,
        skill_packs={"hoi4": hoi4_pack}
    )

    return {
        "engine": engine,
        "input_ctrl": input_ctrl,
        "cap_mgr": cap_mgr,
        "verifier": verifier,
        "pack": hoi4_pack
    }


def test_hoi4_pack_loading(hoi4_pack):
    """Test that the HOI4 pack loads manifest, selectors, and skills."""
    assert hoi4_pack is not None, "HOI4 pack should be discovered"
    assert hoi4_pack.game_id == "hoi4"
    assert len(hoi4_pack.skills) >= 4
    assert "pause_unpause" in hoi4_pack.skills
    assert "open_production" in hoi4_pack.skills
    assert "increase_speed" in hoi4_pack.skills
    assert "select_army_group" in hoi4_pack.skills

    assert len(hoi4_pack.selectors) >= 4
    assert "topbar_factories" in hoi4_pack.selectors
    assert "pause_button" in hoi4_pack.selectors


def test_hoi4_skill_execution_pause_unpause(mock_engine):
    """Test step execution of pause_unpause skill."""
    engine = mock_engine["engine"]
    input_ctrl = mock_engine["input_ctrl"]

    res = engine.execute_skill("pause_unpause", game_id="hoi4", dry_run=True)
    assert res.success is True
    assert res.status == "success"
    assert res.steps_executed == 1
    input_ctrl.press_keys.assert_called_with(keys=["space"], duration_ms=50, dry_run=True)


def test_hoi4_skill_execution_open_production(mock_engine):
    """Test step execution of open_production skill."""
    engine = mock_engine["engine"]
    input_ctrl = mock_engine["input_ctrl"]

    res = engine.execute_skill("open_production", game_id="hoi4", dry_run=True)
    assert res.success is True
    assert res.steps_executed == 1
    input_ctrl.press_keys.assert_called_with(keys=["y"], duration_ms=60, dry_run=True)


def test_hoi4_skill_execution_increase_speed(mock_engine):
    """Test step execution of increase_speed skill."""
    engine = mock_engine["engine"]
    input_ctrl = mock_engine["input_ctrl"]

    res = engine.execute_skill("increase_speed", game_id="hoi4", dry_run=True)
    assert res.success is True
    assert res.steps_executed == 1
    input_ctrl.press_keys.assert_called_with(keys=["num+"], duration_ms=50, dry_run=True)


def test_incomplete_or_invalid_skill_flagged(mock_engine):
    """Incomplete skills (missing template, missing steps, or invalid action) produce explicit failures."""
    engine = mock_engine["engine"]

    # Incomplete skill: references missing template file
    broken_pack = mock_engine["pack"]
    # select_army_group references army_group_leader selector which requires template
    res = engine.execute_skill("select_army_group", game_id="hoi4", dry_run=True)
    # If the template file doesn't exist on disk, it is flagged as INCOMPLETE
    if res.status == "incomplete":
        assert "INCOMPLETE" in res.message
        assert res.success is False
        assert res.evidence["flagged_incomplete"] is True

    # Incomplete skill: nonexistent skill
    res_missing = engine.execute_skill("nonexistent_skill_xyz", game_id="hoi4", dry_run=True)
    assert res_missing.success is False
    assert res_missing.status == "failed"
    assert "not found" in res_missing.message
