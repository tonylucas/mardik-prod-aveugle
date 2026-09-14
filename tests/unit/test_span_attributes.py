"""A span without attributes is a span nobody can query: assert what we tag."""
import pytest
from structlog.testing import capture_logs

from mardik.agent import Agent
from mardik.errors import LLMTimeoutError
from mardik.session import SessionStore
from mardik.tools import DEFAULT_TOOLS


def _spans(exporter):
    return {span.name: span for span in exporter.get_finished_spans()}


def _run(llm, telemetry, store=None, session_id="obs", message="Ma commande #1042 ?"):
    agent = Agent(llm=llm, tools=DEFAULT_TOOLS, telemetry=telemetry)
    return agent.run_turn(store or SessionStore(), session_id, message)


@pytest.mark.parametrize("span_name", ["agent.turn", "llm.invoke", "tool.call"])
def test_every_span_carries_the_session_and_turn(
    span_name, fake_llm, telemetry, span_exporter
):
    _run(fake_llm, telemetry, session_id="sess-42")
    attributes = _spans(span_exporter)[span_name].attributes
    assert attributes["session_id"] == "sess-42"
    assert attributes["turn_index"] == 1


def test_turn_index_increments_across_turns(fake_llm, telemetry, span_exporter):
    store = SessionStore()
    _run(fake_llm, telemetry, store)
    _run(fake_llm, telemetry, store)
    indexes = [
        span.attributes["turn_index"]
        for span in span_exporter.get_finished_spans()
        if span.name == "agent.turn"
    ]
    assert indexes == [1, 2]


def test_llm_span_describes_the_call(fake_llm, telemetry, span_exporter):
    _run(fake_llm, telemetry)
    attributes = _spans(span_exporter)["llm.invoke"].attributes
    assert attributes["gen_ai.operation.name"] == "chat"
    assert attributes["gen_ai.request.message_count"] == 1
    assert attributes["gen_ai.response.tool_call_count"] == 1


def test_tool_span_separates_answered_from_resolved(fake_llm, telemetry, span_exporter):
    _run(fake_llm, telemetry)
    assert _spans(span_exporter)["tool.call"].attributes["tool.status"] == "ok"

    _run(fake_llm, telemetry, message="Ma commande #9999 ?")
    statuses = [
        span.attributes["tool.status"]
        for span in span_exporter.get_finished_spans()
        if span.name == "tool.call"
    ]
    assert statuses == ["ok", "not_found"]


def test_failure_marks_the_spans_in_error(timeout_llm, telemetry, span_exporter):
    with pytest.raises(LLMTimeoutError):
        _run(timeout_llm, telemetry)

    for span in span_exporter.get_finished_spans():
        # StatusCode.ERROR, plus the exception recorded as a span event: this is
        # what makes a failed turn visible in the trace backend instead of just
        # short.
        assert span.status.status_code.name == "ERROR", span.name
        assert [event.name for event in span.events] == ["exception"]


def test_log_carries_the_trace_id(fake_llm, telemetry):
    with capture_logs() as logs:
        _run(fake_llm, telemetry)
    entry = next(log for log in logs if log["event"] == "turn.completed")
    # The bridge between a log line and its trace: 32 hex chars, never zeroes.
    assert len(entry["trace_id"]) == 32
    assert int(entry["trace_id"], 16) != 0


def test_turns_counter_is_emitted_per_session(fake_llm, telemetry, metric_reader):
    _run(fake_llm, telemetry, session_id="counted")
    points = [
        point
        for rm in metric_reader.get_metrics_data().resource_metrics
        for sm in rm.scope_metrics
        for metric in sm.metrics
        if metric.name == "turns_total"
        for point in metric.data.data_points
    ]
    assert [(p.attributes["session_id"], p.value) for p in points] == [("counted", 1)]
