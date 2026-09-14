import pytest

from mardik.agent import Agent
from mardik.errors import LLMTimeoutError
from mardik.runner import load_session, replay
from mardik.session import SessionStore
from mardik.tools import DEFAULT_TOOLS


def _agent(llm, telemetry):
    return Agent(llm=llm, tools=DEFAULT_TOOLS, telemetry=telemetry)


def test_replay_preserves_session_context(fake_llm, telemetry):
    data = load_session("replay_delivery")
    result = replay(data, _agent(fake_llm, telemetry), SessionStore())
    assert "expédiée" in result.reply


def test_replay_smoke(fake_llm, telemetry):
    """End-to-end invariants of a replayed turn.

    "reply is not None" used to be the whole assertion, which an empty string,
    a stack trace or somebody else's answer all satisfy: the test stayed green
    through every defect in this repository. A smoke test should assert that
    the turn crossed the chain, not merely that it returned.
    """
    data = load_session("replay_delivery")
    store = SessionStore()

    result = replay(data, _agent(fake_llm, telemetry), store)

    assert result.session_id == data["session_id"]
    assert result.reply.strip()
    assert store.turns(result.session_id) == 1
    assert store.history(result.session_id) == [
        *data["messages"],
        {"role": "assistant", "content": result.reply},
    ]


def test_replay_timeout_incident(timeout_llm, telemetry):
    data = load_session("incident_timeout")
    with pytest.raises(LLMTimeoutError):
        replay(data, _agent(timeout_llm, telemetry), SessionStore())
