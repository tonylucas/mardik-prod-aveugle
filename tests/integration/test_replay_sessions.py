"""Replay the recorded session corpus, one session at a time and concurrently."""
import pytest

from mardik.agent import Agent
from mardik.runner import load_session, replay, replay_all
from mardik.session import SessionStore
from mardik.tools import DEFAULT_TOOLS


def _agent(llm, telemetry):
    return Agent(llm=llm, tools=DEFAULT_TOOLS, telemetry=telemetry)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("replay_delivery", "expédiée"),
        ("replay_preparation", "en préparation"),
        ("replay_unknown_order", "introuvable"),
        ("replay_missing_order_id", "numéro de commande"),
    ],
)
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


CORPUS = [
    "replay_delivery",
    "replay_preparation",
    "replay_unknown_order",
    "replay_missing_order_id",
]


def test_concurrent_sessions_keep_their_histories_apart(fake_llm, telemetry):
    store = SessionStore()
    results = replay_all(CORPUS, _agent(fake_llm, telemetry), store)

    assert len(results) == len(CORPUS)
    for result in results:
        assert store.turns(result.session_id) == 1
        history = store.history(result.session_id)
        # The last message of a session is the reply this replay produced: no
        # other session's turn may have landed in this history.
        assert history[-1] == {"role": "assistant", "content": result.reply}


def test_concurrent_turns_on_one_session_are_all_counted(fake_llm, telemetry):
    store = SessionStore()
    session_id = load_session("replay_delivery")["session_id"]

    replay_all(["replay_delivery"] * 8, _agent(fake_llm, telemetry), store)

    assert store.turns(session_id) == 8
