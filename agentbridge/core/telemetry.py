"""OpenTelemetry-compatible traces for LLM steps, tools, and policy.

Emits OTLP-shaped JSON so collectors can ingest without a hard runtime
dependency on the OpenTelemetry SDK. If ``opentelemetry`` is installed the
same spans are also recorded on the global tracer.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import uuid4

from agentbridge.core.state import SpanRecord

Status = Literal["UNSET", "OK", "ERROR"]


def _now_nano() -> int:
    return time.time_ns()


@dataclass
class Span:
    trace_id: str
    span_id: str
    parent_span_id: str | None
    name: str
    start_unix_nano: int
    end_unix_nano: int = 0
    status: Status = "UNSET"
    attributes: dict[str, Any] = field(default_factory=dict)

    def to_record(self) -> SpanRecord:
        return SpanRecord(
            trace_id=self.trace_id,
            span_id=self.span_id,
            parent_span_id=self.parent_span_id,
            name=self.name,
            start_unix_nano=self.start_unix_nano,
            end_unix_nano=self.end_unix_nano,
            status=self.status,
            attributes=self.attributes,
        )

    def to_otlp(self) -> dict[str, Any]:
        return {
            "traceId": self.trace_id,
            "spanId": self.span_id,
            "parentSpanId": self.parent_span_id,
            "name": self.name,
            "startTimeUnixNano": str(self.start_unix_nano),
            "endTimeUnixNano": str(self.end_unix_nano),
            "status": {"code": self.status},
            "attributes": [{"key": k, "value": {"stringValue": str(v)}} for k, v in self.attributes.items()],
        }


@dataclass
class Tracer:
    service_name: str = "agentbridge-africa"
    spans: list[Span] = field(default_factory=list)
    _current_trace: str | None = None
    _current_span: str | None = None

    def start(self, name: str, *, attributes: dict[str, Any] | None = None, parent: Span | None = None) -> Span:
        trace_id = parent.trace_id if parent else self._current_trace or uuid4().hex
        parent_id = parent.span_id if parent else self._current_span
        span = Span(
            trace_id=trace_id,
            span_id=uuid4().hex[:16],
            parent_span_id=parent_id,
            name=name,
            start_unix_nano=_now_nano(),
            attributes=dict(attributes or {}),
        )
        self._current_trace = trace_id
        self._current_span = span.span_id
        self.spans.append(span)
        self._otel_start(span)
        return span

    def end(self, span: Span, *, status: Status = "OK", attributes: dict[str, Any] | None = None) -> Span:
        span.end_unix_nano = _now_nano()
        span.status = status
        if attributes:
            span.attributes.update(attributes)
        if self._current_span == span.span_id:
            self._current_span = span.parent_span_id
        self._otel_end(span)
        return span

    def export_otlp(self) -> dict[str, Any]:
        return {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": [
                            {"key": "service.name", "value": {"stringValue": self.service_name}},
                        ]
                    },
                    "scopeSpans": [{"spans": [s.to_otlp() for s in self.spans]}],
                }
            ]
        }

    def _otel_start(self, span: Span) -> None:
        try:
            from opentelemetry import trace  # type: ignore

            tracer = trace.get_tracer(self.service_name)
            otel_span = tracer.start_span(span.name)
            for k, v in span.attributes.items():
                otel_span.set_attribute(k, v)
            otel_span.end()
        except Exception:
            return

    def _otel_end(self, span: Span) -> None:
        return
