"""Internal guard shared by the steps of one native MCP action."""
from dataclasses import dataclass
from typing import Callable

import numpy as np

from .safety import EmergencyStopTriggered, SafetyViolationError
from .types import WindowInfo


class ActionScopeInterrupted(SafetyViolationError):
    """The sequence cannot produce trustworthy evidence or send more input."""


@dataclass(frozen=True)
class NativeActionScope:
    validate_window: Callable[[], WindowInfo]
    capture_window: Callable[[WindowInfo], np.ndarray]

    def require_window(self) -> WindowInfo:
        try:
            return self.validate_window()
        except (SafetyViolationError, EmergencyStopTriggered) as exc:
            raise ActionScopeInterrupted(str(exc)) from exc

    def capture(self) -> np.ndarray:
        window = self.require_window()
        try:
            frame = self.capture_window(window)
        except (SafetyViolationError, EmergencyStopTriggered, RuntimeError) as exc:
            raise ActionScopeInterrupted(str(exc)) from exc
        self.require_window()
        return frame

    def input_kwargs(self) -> dict[str, WindowInfo]:
        return {"target_window": self.require_window()}
