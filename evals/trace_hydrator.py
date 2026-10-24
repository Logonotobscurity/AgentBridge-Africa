"""Hydrate privacy-safe EvalCase records from OTEL and Langfuse traces."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping

from pydantic import BaseModel, Field

from evals.production_sampler import score_trace

_SENSITIVE_KEYS = {
    "account_number", "authorization", "email", "otp", "phone", "pin",
    "recipient", "token", "access_token", "refresh_token",
}


class EvalCase(BaseModel):
    id: str
    source: Literal["otel", "langfuse"]
    input: dict[str, Any]
    expected_output: dict[str, Any]
    metadata: dict[str, Any] = Field(default_factory=dict)
    source_trace_id: str
    source_observation_id: str | None = None


def _redact(value: Any, key: str = "") -> Any:
    if key.lower() in _SENSITIVE_KEYS:
        digest = hashlib.sha256(str(value).encode()).hexdigest()[:12]
        return f"[redacted:{digest}]"
    if isinstance(value, Mapping):
        return {str(k): _redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _attributes(raw: Any) -> dict[str, Any]:
    if isinstance(raw, Mapping):
        return dict(raw)
    result: dict[str, Any] = {}
    for item in raw or []:
        if not isinstance(item, Mapping) or "key" not in item:
            continue
        value = item.get("value")
        if isinstance(value, Mapping):
            value = next(iter(value.values()), None)
        result[str(item["key"])] = value
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
    """Convert an OTLP JSON export or a normalized ``spans`` payload."""
    spans: list[Mapping[str, Any]] = []
    if isinstance(payload.get("spans"), list):
        spans.extend(s for s in payload["spans"] if isinstance(s, Mapping))
    for resource in payload.get("resourceSpans", []) or []:
        for scope in resource.get("scopeSpans", []) or []:
            spans.extend(s for s in scope.get("spans", []) or [] if isinstance(s, Mapping))

    cases: list[EvalCase] = []
    for span in spans:
        attrs = _attributes(span.get("attributes"))
        tool = attrs.get("tool") or attrs.get("tool.name") or span.get("name")
        arguments = _json_object(attrs.get("tool.arguments") or attrs.get("arguments") or {})
        normalized = {
            "id": span.get("spanId") or span.get("span_id"),
            "tool": tool,
            "arguments": arguments,
            "oauth_scopes": attrs.get("oauth.scopes") or attrs.get("oauth_scopes") or [],
            "retry_count": attrs.get("payment.retry_count", 0),
            "max_retries": attrs.get("payment.max_retries", 3),
            "mutated_ledger": attrs.get("payment.mutated_ledger", False),
        }
        score = score_trace(normalized)
        trace_id = str(span.get("traceId") or span.get("trace_id") or "")
        cases.append(
            EvalCase(
                id=f"otel_{normalized['id'] or trace_id}",
                source="otel",
                input=_redact({"tool": tool, "arguments": arguments}),
                expected_output={"pass": score["pass"], "tool": tool},
                metadata={"score": score, "span_name": span.get("name")},
                source_trace_id=trace_id,
                source_observation_id=str(normalized["id"] or "") or None,
            )
        )
    return cases


def from_langfuse(traces: Iterable[Mapping[str, Any]]) -> list[EvalCase]:
    """Convert Langfuse trace API records into the same EvalCase contract."""
    cases: list[EvalCase] = []
    for trace in traces:
        trace_id = str(trace.get("id") or trace.get("trace_id") or "")
        raw_input = _json_object(trace.get("input"))
        raw_output = _json_object(trace.get("output"))
        normalized = {
            "id": trace_id,
            "tool": raw_input.get("tool") or trace.get("name"),
            "arguments": raw_input.get("arguments") or {},
            "oauth_scopes": (trace.get("metadata") or {}).get("oauth_scopes", []),
            "retry_count": (trace.get("metadata") or {}).get("retry_count", 0),
            "max_retries": (trace.get("metadata") or {}).get("max_retries", 3),
        }
        score = score_trace(normalized)
        cases.append(
            EvalCase(
                id=f"langfuse_{trace_id}",
                source="langfuse",
                input=_redact(raw_input),
                expected_output=_redact(raw_output) or {"pass": score["pass"]},
                metadata={"score": score, "tags": trace.get("tags") or []},
                source_trace_id=trace_id,
            )
        )
    return cases


def write_eval_cases(cases: Iterable[EvalCase], path: Path) -> Path:
    """Write deterministic JSONL suitable for CI and later dataset upload."""
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [case.model_dump_json() for case in cases]
    path.write_text("\n".join(rows) + ("\n" if rows else ""), encoding="utf-8")
    return path


def publish_to_langfuse(cases: Iterable[EvalCase], client: Any, dataset_name: str) -> int:
    """Upload hydrated cases while retaining links to production traces."""
    count = 0
    for case in cases:
        client.create_dataset_item(
            dataset_name=dataset_name,
            input=case.input,
            expected_output=case.expected_output,
            metadata=case.metadata,
            source_trace_id=case.source_trace_id,
            source_observation_id=case.source_observation_id,
        )
        count += 1
    return count
