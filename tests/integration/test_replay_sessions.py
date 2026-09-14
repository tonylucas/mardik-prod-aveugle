"""Replay the recorded session corpus, one session at a time and concurrently."""
import pytest

from mardik.agent import Agent
from mardik.runner import load_session, replay, replay_all
from mardik.session import SessionStore
from mardik.tools import DEFAULT_TOOLS


def _agent(llm, telemetry):
    return Agent(llm=llm, tools=DEFAULT_TOOLS, telemetry=telemetry)


CORPUS = {
    "replay_delivery": "expédiée",
    "replay_preparation": "en préparation",
    "replay_unknown_order": "introuvable",
    "replay_missing_order_id": "numéro de commande",
}


@pytest.mark.parametrize(("name", "expected"), CORPUS.items())
def test_each_recorded_session_reaches_its_expected_outcome(
    name, expected, fake_llm, telemetry
):
    result = replay(load_session(name), _agent(fake_llm, telemetry), SessionStore())
    assert expected in result.reply


def test_session_without_order_id_calls_no_tool(fake_llm, telemetry, span_exporter):
    replay(
        load_session("replay_missing_order_id"),
        _agent(fake_llm, telemetry),
        SessionStore(),
    )
    span_names = [span.name for span in span_exporter.get_finished_spans()]
    assert "tool.call" not in span_names


def test_concurrent_sessions_do_not_see_each_other_messages(fake_llm, telemetry):
    """No session may end up holding a message that belongs to another one.

    Asserted twice over, because the two failures look nothing alike: the
    history must match the recording message for message, and the reply must
    still be the one this session's own order id leads to. A leaked order id
    makes the agent answer about somebody else's parcel while every history
    still looks plausible.
    """
    store = SessionStore()
    names = list(CORPUS)
    sessions = [load_session(name) for name in names]

    results = replay_all(names, _agent(fake_llm, telemetry), store)

    assert len(results) == len(names)
    for name, session, result in zip(names, sessions, results):
        expected_history = [
            *session["messages"],
            {"role": "assistant", "content": result.reply},
        ]
        assert store.history(result.session_id) == expected_history
        assert store.turns(result.session_id) == 1
        assert CORPUS[name] in result.reply


def test_concurrent_turns_on_one_session_are_all_counted(fake_llm, telemetry):
    store = SessionStore()
    session_id = load_session("replay_delivery")["session_id"]

    replay_all(["replay_delivery"] * 8, _agent(fake_llm, telemetry), store)

    assert store.turns(session_id) == 8
