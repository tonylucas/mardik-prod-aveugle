"""Replay recorded sessions through the agent for integration testing."""
from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from .agent import Agent, TurnResult
from .session import SessionStore


def sessions_dir() -> Path:
    return Path(os.environ.get("MARDIK_SESSIONS_DIR", "sessions"))


def load_session(name: str) -> dict[str, Any]:
    path = sessions_dir() / f"{name}.json"
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def replay(session_data: dict[str, Any], agent: Agent, store: SessionStore) -> TurnResult:
    """Replay a recorded session and return the result of its final turn.

    Every message that precedes the final one is seeded into the store first:
    a turn is only faithful to the recording if the agent sees the same
    conversation history the real one had.
    """
    session_id = session_data["session_id"]
    messages = session_data["messages"]
    for message in messages[:-1]:
        store.append(session_id, message)
    return agent.run_turn(store, session_id, messages[-1]["content"])


def replay_all(names: list[str], agent: Agent, store: SessionStore) -> list[TurnResult]:
    """Replay several recorded sessions concurrently on a shared store.

    This is the multi-session path: real traffic never arrives one session at a
    time, and a store that is correct sequentially can still lose turns under
    concurrency. Passing the same name twice replays that session twice, which
    puts two concurrent turns on a single session id.
    """
    sessions = [load_session(name) for name in names]
    with ThreadPoolExecutor(max_workers=len(sessions)) as pool:
        futures = [pool.submit(replay, data, agent, store) for data in sessions]
        return [future.result() for future in futures]
