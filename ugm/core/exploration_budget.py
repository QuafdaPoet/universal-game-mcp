"""Bounded, process-local scan quotas for opt-in, read-only goal exploration."""
from dataclasses import dataclass
from threading import Lock
import time
from uuid import uuid4

from .safety import SafetyViolationError

MAX_SCANS = 16
MAX_SESSIONS = 8
SESSION_TTL_SECONDS = 600


@dataclass
class _Session:
    scope: object
    deadline: float
    remaining: int
    active: bool = False


class ExplorationBudget:
    def __init__(self) -> None:
        self._lock = Lock()
        self._sessions: dict[str, _Session] = {}

    def reserve(
        self, scope: object, session_id: str | None, budget: int | None,
    ) -> str:
        """Reserve before capture so a failed scan cannot evade the quota."""
        with self._lock:
            now = time.monotonic()
            for sid, session in list(self._sessions.items()):
                if now >= session.deadline and not session.active:
                    del self._sessions[sid]
            if session_id is None:
                if budget is None or not 1 <= budget <= MAX_SCANS:
                    raise ValueError("Invalid exploration scan budget.")
                if len(self._sessions) >= MAX_SESSIONS:
                    # Exhausted sessions cannot reserve work; they may be removed early.
                    for sid, session in self._sessions.items():
                        if session.remaining == 0 and not session.active:
                            del self._sessions[sid]
                            break
                if len(self._sessions) >= MAX_SESSIONS:
                    raise ValueError("Too many active exploration sessions; wait for one to finish or expire.")
                session_id = uuid4().hex
                self._sessions[session_id] = _Session(scope, now + SESSION_TTL_SECONDS, budget)
            session = self._sessions.get(session_id)
            if session is None or now >= session.deadline:
                raise ValueError("Exploration session is unknown or expired; start a new session.")
            if scope != session.scope:
                raise SafetyViolationError("Exploration session scope changed; start a new session.")
            if session.active:
                raise ValueError("An exploration scan is already running for this session.")
            if session.remaining <= 0:
                raise ValueError("Exploration session scan budget exhausted.")
            session.active = True
            session.remaining -= 1
            return session_id

    def finish(self, session_id: str, *, discard: bool = False) -> dict[str, int | str]:
        with self._lock:
            session = self._sessions[session_id]
            if discard:
                del self._sessions[session_id]
                return {}
            session.active = False
            remaining_seconds = max(0, int(session.deadline - time.monotonic()))
            return {
                "id": session_id,
                "scans_remaining": session.remaining,
                "remaining_seconds": remaining_seconds,
            }
