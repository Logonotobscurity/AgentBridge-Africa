"""OpenTelemetry-compatible traces with source-side privacy minimization.

Sensitive and unclassified free-form values are minimized before a Span is
stored, converted to a record, or sent to the optional OpenTelemetry SDK.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import uuid4

from agentbridge.core.sensitive_data import (
    OMITTED,
    POLICY_VERSION,
    redact_sensitive_data,
)
from agentbridge.core.state import SpanRecord

Status = Literal["UNSET", "OK", "ERROR"]


def _now_nano() -> int:
    return time.time_ns()


def _safe_span_name(value: Any) -> str:
    result = redact_sensitive_data({"span_name": value})
    candidate = result.get("span_name", OMITTED)
    return candidate if isinstance(candidate, str) else OMITTED


def _safe_attributes(attributes: dict[str, Any]) -> dict[str, Any]:
    result = redact_sensitive_data(attributes)
    if not isinstance(result, dict):
        result = {}
    result["privacy_policy_version"] = POLICY_VERSION
    return result


def _safe_ids(trace_id: str, span_id: str, parent_span_id: str | None) -> dict[str, Any]:
    return redact_sensitive_data({
        "trace_id": trace_id,
        "span_id": span_id,
        "parent_span_id": parent_span_id,
    })


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
        ids = _safe_ids(self.trace_id, self.span_id, self.parent_span_id)
        return SpanRecord(
            trace_id=ids["trace_id"],
            span_id=ids["span_id"],
            parent_span_id=ids["parent_span_id"],
            name=_safe_span_name(self.name),
            start_unix_nano=self.start_unix_nano,
            end_unix_nano=self.end_unix_nano,
            status=self.status,
            attributes=_safe_attributes(self.attributes),
        )

    def to_otlp(self) -> dict[str, Any]:
        ids = _safe_ids(self.trace_id, self.span_id, self.parent_span_id)
        attributes = _safe_attributes(self.attributes)
        return {
            "traceId": ids["trace_id"],
            "spanId": ids["span_id"],
            "parentSpanId": ids["parent_span_id"],
            "name": _safe_span_name(self.name),
            "startTimeUnixNano": str(self.start_unix_nano),
            "endTimeUnixNano": str(self.end_unix_nano),
            "status": {"code": self.status},
            "attributes": [
                {"key": key, "value": {"stringValue": str(value)}}
                for key, value in attributes.items()
            ],
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
            name=_safe_span_name(name),
            start_unix_nano=_now_nano(),
            attributes=_safe_attributes(dict(attributes or {})),
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
            span.attributes.update(_safe_attributes(attributes))
        if self._current_span == span.span_id:
            self._current_span = span.parent_span_id
        self._otel_end(span)
        return span

    def export_otlp(self) -> dict[str, Any]:
        resource = _safe_attributes({"service.name": self.service_name})
        return {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": [
                            {"key": "service.name", "value": {"stringValue": str(resource.get("service.name", OMITTED))}},
                            {"key": "privacy.policy.version", "value": {"stringValue": POLICY_VERSION}},
                        ]
                    },
                    "scopeSpans": [{"spans": [span.to_otlp() for span in self.spans]}],
                }
            ]
        }

    def _otel_start(self, span: Span) -> None:
        try:
            from opentelemetry import trace  # type: ignore

            tracer = trace.get_tracer(self.service_name)
            otel_span = tracer.start_span(_safe_span_name(span.name))
            for key, value in _safe_attributes(span.attributes).items():
                if isinstance(value, (str, bool, int, float)):
                    otel_span.set_attribute(key, value)
                elif isinstance(value, (list, tuple)) and all(
                    isinstance(item, (str, bool, int, float)) for item in value
                ):
                    otel_span.set_attribute(key, tuple(value))
            otel_span.end()
        except Exception:
            return

    def _otel_end(self, span: Span) -> None:
        return
