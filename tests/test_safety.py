"""
Unit tests for UGM safety, emergency stop, bounds checking, and anti-cheat protection.
"""

import pytest
from ugm.core.safety import (
    SafetyManager,
    safety_manager,
    EmergencyStopTriggered,
    SafetyViolationError
)


def setup_function():
    safety_manager.reset_emergency_stop()


def test_emergency_stop_trigger_and_check():
    assert safety_manager.is_stopped is False
    safety_manager.check_safety()  # Should not raise

    res = safety_manager.trigger_emergency_stop("Test emergency stop")
    assert res["emergency_stopped"] is True
    assert safety_manager.is_stopped is True

    with pytest.raises(EmergencyStopTriggered) as excinfo:
        safety_manager.check_safety()
    assert "Test emergency stop" in str(excinfo.value)

    reset_res = safety_manager.reset_emergency_stop()
    assert reset_res["emergency_stopped"] is False
    assert safety_manager.is_stopped is False
    safety_manager.check_safety()  # Should succeed again


def test_normalized_coordinate_validation():
    # Valid
    safety_manager.validate_normalized_coordinates(0.0, 0.0)
    safety_manager.validate_normalized_coordinates(0.5, 0.5)
    safety_manager.validate_normalized_coordinates(1.0, 1.0)

    # Invalid
    with pytest.raises(SafetyViolationError):
        safety_manager.validate_normalized_coordinates(-0.01, 0.5)

    with pytest.raises(SafetyViolationError):
        safety_manager.validate_normalized_coordinates(1.05, 0.5)

    with pytest.raises(SafetyViolationError):
        safety_manager.validate_normalized_coordinates(0.5, -0.1)


def test_anti_cheat_blacklist_block():
    # Valid offline processes
    safety_manager.validate_process("hoi4.exe")
    safety_manager.validate_process("civilizationVI.exe")
    safety_manager.validate_process("python.exe")

    # Blacklisted processes
    with pytest.raises(SafetyViolationError):
        safety_manager.validate_process("EasyAntiCheat_x64.exe")

    with pytest.raises(SafetyViolationError):
        safety_manager.validate_process("vanguard.exe")
