"""In-memory session store shared across concurrent agent turns."""
from __future__ import annotations

import threading
import time
from typing import Any


class SessionStore:
    """Holds conversation history and a per-session turn counter.

    A single store instance is shared by every worker handling a session, so
    several turns for the same session can land concurrently.
    """

    def __init__(self) -> None:
        self._history: dict[str, list[dict[str, Any]]] = {}
        self._turns: dict[str, int] = {}
        # ponytail: one lock for the whole store; split per session if
        # contention ever shows up in the latency histogram.
        self._lock = threading.Lock()

    def append(self, session_id: str, message: dict[str, Any]) -> None:
        with self._lock:
            self._history.setdefault(session_id, []).append(message)

    def history(self, session_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._history.get(session_id, []))

    def record_turn(self, session_id: str) -> int:
        """Count one more turn for this session and return its 1-based index."""
        # Read-modify-write: without the lock, concurrent turns of the same
        # session overwrite each other's count.
        with self._lock:
            count = self._turns.get(session_id, 0)
            # Touching the back-office accounting takes a moment.
            time.sleep(0.0005)
            self._turns[session_id] = count + 1
            return count + 1

    def turns(self, session_id: str) -> int:
        with self._lock:
            return self._turns.get(session_id, 0)
