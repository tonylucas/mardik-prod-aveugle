"""The Mardik agent: turns a user message into a reply, calling tools as needed."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from opentelemetry import context as otel_context

from .errors import LLMTimeoutError, MardikError
from .session import SessionStore
from .telemetry import NoOpTelemetry


@dataclass
class Reply:
    content: str
    tool_calls: list[dict[str, Any]]


@dataclass
class TurnResult:
    session_id: str
    reply: str


def _tag(span: Any, attributes: dict[str, Any]) -> None:
    for key, value in attributes.items():
        span.set_attribute(key, value)


class LLM(Protocol):
    def invoke(self, messages: list[dict[str, Any]]) -> Reply: ...


class Agent:
    def __init__(
        self,
        llm: LLM,
        tools: dict[str, Callable[..., str]],
        telemetry: Any | None = None,
    ) -> None:
        self.llm = llm
        self._tools = tools
        self.telemetry = telemetry if telemetry is not None else NoOpTelemetry()

    def _invoke_llm_sync(
        self, messages: list[dict[str, Any]], attributes: dict[str, Any]
    ) -> Reply:
        with self.telemetry.tracer.start_as_current_span("llm.invoke") as span:
            _tag(span, attributes)
            span.set_attribute("gen_ai.operation.name", "chat")
            span.set_attribute("gen_ai.request.message_count", len(messages))
            # The production adapter carries the deployment name; the fake LLMs
            # of the test suite do not, and the attribute is simply absent.
            model = getattr(self.llm, "model_name", None)
            if model is not None:
                span.set_attribute("gen_ai.request.model", model)
            try:
                reply = self.llm.invoke(messages)
            except TimeoutError as exc:
                raise LLMTimeoutError(str(exc)) from exc
            span.set_attribute("gen_ai.response.tool_call_count", len(reply.tool_calls))
            return reply

    def _invoke_llm(
        self, messages: list[dict[str, Any]], attributes: dict[str, Any]
    ) -> Reply:
        # The Azure SDK call is blocking, so run it on a worker thread. The
        # OpenTelemetry context is thread-local: capture it here and re-attach
        # it inside the worker, otherwise llm.invoke opens a trace of its own.
        box: dict[str, Any] = {}
        parent = otel_context.get_current()

        def worker() -> None:
            token = otel_context.attach(parent)
            try:
                box["reply"] = self._invoke_llm_sync(messages, attributes)
            except BaseException as exc:  # re-raised on the calling thread
                box["error"] = exc
            finally:
                otel_context.detach(token)

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()
        if "error" in box:
            raise box["error"]
        return box["reply"]

    def _dispatch_tool(self, call: dict[str, Any], attributes: dict[str, Any]) -> str:
        with self.telemetry.tracer.start_as_current_span("tool.call") as span:
            _tag(span, attributes)
            span.set_attribute("tool.name", call["name"])
            tool = self._tools[call["name"]]
            result = tool(**call["args"])
            # "called" and "helped the user" are not the same thing: an order
            # absent from the back-office answers without resolving anything.
            span.set_attribute("tool.status", "not_found" if "introuvable" in result else "ok")
            return result

    def run_turn(
        self, store: SessionStore, session_id: str, user_message: str
    ) -> TurnResult:
        with self.telemetry.tracer.start_as_current_span("agent.turn") as span:
            start = time.perf_counter()
            store.append(session_id, {"role": "user", "content": user_message})
            turn_index = store.record_turn(session_id)
            # Carried down to every child span: without it, concurrent sessions
            # are indistinguishable in the trace backend.
            attributes = {"session_id": session_id, "turn_index": turn_index}
            _tag(span, attributes)
            self.telemetry.turns.add(1, {"session_id": session_id})

            try:
                reply = self._invoke_llm(store.history(session_id), attributes)
                text = reply.content
                for call in reply.tool_calls:
                    text = self._dispatch_tool(call, attributes)
            except MardikError:
                self.telemetry.errors.add(1, {"session_id": session_id})
                raise
            finally:
                elapsed_ms = (time.perf_counter() - start) * 1000.0
                self.telemetry.record_latency(elapsed_ms, session_id=session_id)

            store.append(session_id, {"role": "assistant", "content": text})
            self.telemetry.logger.info(
                "turn.completed",
                session_id=session_id,
                turn_index=turn_index,
                duration_ms=round(elapsed_ms, 1),
                trace_id=format(span.get_span_context().trace_id, "032x"),
            )
            return TurnResult(session_id=session_id, reply=text)
