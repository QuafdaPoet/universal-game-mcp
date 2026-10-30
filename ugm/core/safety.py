"""
Universal Game MCP (UGM) - Safety, Dry-run & Emergency Stop Manager
Ensures actions are strictly constrained to target game window, offline environments,
and can be halted immediately at any instant.
"""

from __future__ import annotations
import threading
import time
from typing import Optional, Callable, Any
import logging

logger = logging.getLogger("ugm.safety")


class EmergencyStopTriggered(Exception):
    """Raised when an action is attempted while emergency stop is active."""
    pass


class SafetyViolationError(ValueError):
    """Raised when an operation violates containment or safety boundaries."""
    pass


class SafetyManager:
    """
    Singleton thread-safe safety and emergency stop manager.
    """
    _instance: Optional[SafetyManager] = None
    _lock = threading.Lock()

    def __new__(cls) -> SafetyManager:
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._is_stopped = False
                cls._instance._stop_reason = ""
                cls._instance._stop_time = 0.0
                cls._instance._dry_run_enforced = False
                cls._instance._anti_cheat_blacklisted = [
                    "easyanticheat", "battleye", "vanguard", "ricochet", "vac",
                    "equ8", "beclient", "start_protected_game",
                    "vgc.exe", "beservice.exe", "easyanticheat.exe", "faceit.exe",
                    "vgc", "beservice", "faceit", "punkbuster", "pnkbstr"
                ]
            return cls._instance

    @property
    def is_stopped(self) -> bool:
        return self._is_stopped

    @property
    def stop_reason(self) -> str:
        return self._stop_reason

    @property
    def is_dry_run_enforced(self) -> bool:
        return self._dry_run_enforced

    def set_dry_run_enforced(self, enforced: bool = True) -> None:
        """Enforces dry-run globally across all tools and inputs."""
        with self._lock:
            self._dry_run_enforced = enforced
            if enforced:
                logger.info("Global dry-run ENFORCED by SafetyManager.")
            else:
                logger.info("Global dry-run lifted by SafetyManager.")

    def trigger_emergency_stop(self, reason: str = "User requested emergency stop") -> dict[str, Any]:
        """Halts all input and execution immediately."""
        with self._lock:
            self._is_stopped = True
            self._stop_reason = reason
            self._stop_time = time.time()
            logger.warning("EMERGENCY STOP TRIGGERED: %s", reason)
            return {
                "emergency_stopped": True,
                "reason": self._stop_reason,
                "timestamp": self._stop_time,
            }

    def reset_emergency_stop(self) -> dict[str, Any]:
        """Resets the emergency stop state to allow actions again."""
        with self._lock:
            old_reason = self._stop_reason
            self._is_stopped = False
            self._stop_reason = ""
            self._stop_time = 0.0
            logger.info("Emergency stop has been reset. Previous reason: %s", old_reason)
            return {
                "emergency_stopped": False,
                "message": "Emergency stop state reset successfully.",
            }

    def check_safety(self) -> None:
        """Throws EmergencyStopTriggered if the killswitch is active."""
        if self._is_stopped:
            raise EmergencyStopTriggered(
                f"Action blocked: Emergency stop is active (Reason: {self._stop_reason})"
            )

    def scan_system_processes(self) -> list[str]:
        """
        Scans running system processes using psutil if available.
        Returns list of detected blacklisted process names.
        Gracefully catches ImportError, psutil.AccessDenied, psutil.NoSuchProcess,
        and OSError without crashing.
        If blacklisted processes are detected, triggers emergency stop and raises SafetyViolationError.
        """
        detected: list[str] = []
        try:
            import psutil
            if psutil is None:
                return detected
        except (ImportError, NameError):
            logger.debug("psutil is not installed; skipping system process scan.")
            return detected

        try:
            for proc in psutil.process_iter(['name']):
                try:
                    name = None
                    if getattr(proc, 'info', None) and 'name' in proc.info:
                        name = proc.info['name']
                    if not name and hasattr(proc, 'name'):
                        name = proc.name()
                    if not name:
                        continue
                    name_lower = str(name).lower()
                    for blacklisted in self._anti_cheat_blacklisted:
                        b_lower = blacklisted.lower()
                        if b_lower in name_lower or name_lower == b_lower:
                            detected.append(str(name))
                            break
                except (getattr(psutil, 'NoSuchProcess', Exception),
                        getattr(psutil, 'AccessDenied', Exception),
                        getattr(psutil, 'ZombieProcess', Exception),
                        OSError):
                    continue
                except Exception as e:
                    logger.debug("Error inspecting individual process: %s", e)
                    continue
        except (getattr(psutil, 'AccessDenied', Exception), OSError) as e:
            logger.debug("psutil process_iter restricted: %s", e)
            return detected
        except Exception as e:
            logger.debug("Unexpected error during system process scan: %s", e)
            return detected

        if detected:
            proc_str = ", ".join(detected)
            self.trigger_emergency_stop(
                f"Blocked anti-cheat protected process(es) running in system: {proc_str}. UGM only supports offline games."
            )
            raise SafetyViolationError(
                f"Security policy violation: Detected running anti-cheat process(es): {proc_str}."
            )

        return detected

    def validate_process(self, process_name: str) -> None:
        """
        Ensures the game process is not protected by an online anti-cheat engine.
        Universal Game MCP strictly disallows running on anti-cheat protected online games.
        """
        proc_lower = process_name.lower()
        for blacklisted in self._anti_cheat_blacklisted:
            b_lower = blacklisted.lower()
            if b_lower in proc_lower or proc_lower == b_lower:
                self.trigger_emergency_stop(
                    f"Blocked anti-cheat protected process: {process_name}. UGM only supports offline games."
                )
                raise SafetyViolationError(
                    f"Security policy violation: Detected anti-cheat '{blacklisted}' in '{process_name}'."
                )

        # Defense-in-depth: scan background system processes
        self.scan_system_processes()

    def validate_normalized_coordinates(self, x: float, y: float) -> None:
        """Validates that coordinates are safely normalized [0.0, 1.0]."""
        if not (0.0 <= x <= 1.0) or not (0.0 <= y <= 1.0):
            raise SafetyViolationError(
                f"Coordinates out of bounds: ({x}, {y}). Coordinates must be normalized between 0.0 and 1.0."
            )


# Global singleton helper
safety_manager = SafetyManager()
