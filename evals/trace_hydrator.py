"""Hydrate minimized EvalCase records from OTEL and Langfuse traces."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping

from pydantic import BaseModel, Field

from agentbridge.core.sensitive_data import (
    POLICY_VERSION,
    redact_sensitive_data,
    safe_identifier,
)
from evals.production_sampler import score_trace


class EvalCase(BaseModel):
    id: str
    source: Literal["otel", "langfuse"]
    input: dict[str, Any]
    expected_output: dict[str, Any]
    metadata: dict[str, Any] = Field(default_factory=dict)
    source_trace_id: str
    source_observation_id: str | None = None


def _redact(value: Any, key: str = "") -> Any:
    """Compatibility wrapper around the shared, versioned privacy policy."""
    return redact_sensitive_data(value, key=key)


def _otlp_attribute_value(value: Any) -> Any:
    """Unwrap OTLP AnyValue payloads, including typed arrays and kv-lists."""
    if not isinstance(value, Mapping):
        return value
    for field in ("stringValue", "boolValue", "intValue", "doubleValue", "bytesValue"):
        if field in value:
            return value[field]
    array = value.get("arrayValue")
    if isinstance(array, Mapping):
        return [_otlp_attribute_value(item) for item in array.get("values", [])]
    nested = value.get("kvlistValue")
    if isinstance(nested, Mapping):
        return _attributes(nested.get("values", []))
    return next(iter(value.values()), None)


def _attributes(raw: Any) -> dict[str, Any]:
    if isinstance(raw, Mapping):
        return dict(raw)
    result: dict[str, Any] = {}
    for item in raw or []:
        if not isinstance(item, Mapping) or "key" not in item:
            continue
        result[str(item["key"])] = _otlp_attribute_value(item.get("value"))
    return result


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return dict(parsed) if isinstance(parsed, Mapping) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def from_otel(payload: Mapping[str, Any]) -> list[EvalCase]:
    """Convert an OTLP JSON export or a normalized spans payload."""
    spans: list[Mapping[str, Any]] = []
    if isinstance(payload.get("spans"), list):
        spans.extend(s for s in payload["spans"] if isinstance(s, Mapping))
    for resource in payload.get("resourceSpans", []) or []:
        if not isinstance(resource, Mapping):
            continue
        for scope in resource.get("scopeSpans", []) or []:
            if isinstance(scope, Mapping):
                spans.extend(s for s in scope.get("spans", []) or [] if isinstance(s, Mapping))

    cases: list[EvalCase] = []
    for span in spans:
        attrs = _attributes(span.get("attributes"))
        tool = attrs.get("tool") or attrs.get("tool.name") or span.get("name")
        arguments = _json_object(attrs.get("tool.arguments") or attrs.get("arguments") or {})
        trace_id = safe_identifier(span.get("traceId") or span.get("trace_id"))
        span_id = safe_identifier(span.get("spanId") or span.get("span_id"))
        normalized = {
            "id": span_id or trace_id,
            "tool": tool,
            "arguments": arguments,
            "oauth_scopes": attrs.get("oauth.scopes") or attrs.get("oauth_scopes") or [],
            "retry_count": attrs.get("payment.retry_count", 0),
            "max_retries": attrs.get("payment.max_retries", 3),
            "mutated_ledger": attrs.get("payment.mutated_ledger", False),
        }
        score = score_trace(normalized)
        cases.append(
            EvalCase(
                id=f"otel_{span_id or trace_id or 'missing'}",
                source="otel",
                input=_redact({"tool": tool, "arguments": arguments}),
                expected_output=_redact({"pass": score["pass"], "tool": tool}),
                metadata=_redact({
                    "score": score,
                    "span_name": span.get("name"),
                    "privacy_policy_version": POLICY_VERSION,
                }),
                source_trace_id=trace_id,
                source_observation_id=span_id or None,
            )
        )
    return cases


def from_langfuse(traces: Iterable[Mapping[str, Any]]) -> list[EvalCase]:
    """Convert Langfuse trace API records into the minimized EvalCase contract."""
    cases: list[EvalCase] = []
    for trace in traces:
        trace_id = safe_identifier(trace.get("id") or trace.get("trace_id"))
        raw_input = _json_object(trace.get("input"))
        raw_output = _json_object(trace.get("output"))
        raw_metadata = trace.get("metadata")
        metadata = raw_metadata if isinstance(raw_metadata, Mapping) else {}
        tool = raw_input.get("tool") or trace.get("name")
        normalized = {
            "id": trace_id,
            "tool": tool,
            "arguments": raw_input.get("arguments") or {},
            "oauth_scopes": metadata.get("oauth_scopes", []),
            "retry_count": metadata.get("retry_count", 0),
            "max_retries": metadata.get("max_retries", 3),
        }
        score = score_trace(normalized)
        cases.append(
            EvalCase(
                id=f"langfuse_{trace_id or 'missing'}",
                source="langfuse",
                input=_redact(raw_input),
                expected_output=_redact(raw_output) or {"pass": score["pass"]},
                metadata=_redact({
                    "score": score,
                    "tags": trace.get("tags") or [],
                    "privacy_policy_version": POLICY_VERSION,
                }),
                source_trace_id=trace_id,
            )
        )
    return cases


def _safe_case_payload(case: EvalCase) -> dict[str, Any]:
    """Re-apply policy at each persistence/export sink, even for manual cases."""
    payload = case.model_dump(mode="json")
    safe = _redact(payload)
    return safe if isinstance(safe, dict) else {}


def write_eval_cases(cases: Iterable[EvalCase], path: Path) -> Path:
    """Write minimized JSONL; never serialize caller-provided fields directly."""
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        json.dumps(_safe_case_payload(case), sort_keys=True, separators=(",", ":"))
        for case in cases
    ]
    path.write_text("\n".join(rows) + ("\n" if rows else ""), encoding="utf-8")
    return path


def publish_to_langfuse(cases: Iterable[EvalCase], client: Any, dataset_name: str) -> int:
    """Publish only sink-sanitized fields to a Langfuse evaluation dataset."""
    count = 0
    for case in cases:
        safe = _safe_case_payload(case)
        client.create_dataset_item(
            dataset_name=dataset_name,
            input=safe.get("input", {}),
            expected_output=safe.get("expected_output", {}),
            metadata=safe.get("metadata", {}),
            source_trace_id=safe.get("source_trace_id", ""),
            source_observation_id=safe.get("source_observation_id"),
        )
        count += 1
    return count
