"""Replay the session corpus through the real OTLP exporter.

Produces the traces that back the observability deliverable: one trace per
session, each with its agent.turn / llm.invoke / tool.call tree. Run it with
Jaeger up (`make up`) and read the result on http://localhost:16686.

    set -a; source .env; set +a
    uv run python scripts/emit_traces.py

The LLM is scripted rather than remote: the point is to exercise the
instrumentation, and a recorded session must replay identically every time.
"""
from __future__ import annotations

import re
from typing import Any

from mardik.agent import Agent, Reply
from mardik.config import load_settings
from mardik.runner import replay_all
from mardik.session import SessionStore
from mardik.telemetry import build_default_telemetry
from mardik.tools import DEFAULT_TOOLS

CORPUS = [
    "replay_delivery",
    "replay_preparation",
    "replay_unknown_order",
    "replay_missing_order_id",
]


class ScriptedLLM:
    """Mirrors the test double: an order id anywhere in the history -> lookup."""

    def invoke(self, messages: list[dict[str, Any]]) -> Reply:
        text = " ".join(str(m.get("content", "")) for m in messages)
        match = re.search(r"#(\d+)", text)
        if match:
            return Reply(
                content="",
                tool_calls=[{"name": "lookup_order", "args": {"order_id": match.group(1)}}],
            )
        return Reply(content="Pouvez-vous indiquer votre numéro de commande ?", tool_calls=[])


def main() -> None:
    settings = load_settings()
    agent = Agent(
        llm=ScriptedLLM(),
        tools=DEFAULT_TOOLS,
        telemetry=build_default_telemetry(level=settings.log_level),
    )
    for result in replay_all(CORPUS, agent, SessionStore()):
        print(f"{result.session_id} -> {result.reply}")


if __name__ == "__main__":
    main()
