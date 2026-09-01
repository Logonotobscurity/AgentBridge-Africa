"""Versioned orchestrator state — treat as a public API.

``schema_version`` is additive. New fields MUST be optional with defaults so
in-flight workflows survive rolling deploys without deserialization errors.
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

AGENT_STATE_SCHEMA_VERSION = 2

Connectivity = Literal["stable", "intermittent", "offline_first"]
RunStatus = Literal[
    "running",
    "success",
    "failed",
    "budget_exceeded",
    "awaiting_hitl",
    "degraded",
]


class ContextProfile(BaseModel):
    model_config = ConfigDict(extra="ignore")

    locale: str = "en-NG"
    currency: str = "NGN"
    id_formats: list[str] = Field(default_factory=lambda: ["NIN", "BVN"])
    payment_rails: list[str] = Field(default_factory=lambda: ["bank", "ussd", "mobile_money"])
    connectivity: Connectivity = "intermittent"
    max_tool_latency_ms: int = 8000
    max_run_cost_usd: float = 0.15
    language_preference: str = "en"
    # v2 optional
    hitl_amount_threshold: float = 100_000.0
    oauth_required: bool = False
    aml_daily_limit: float = 1_000_000.0


class StepResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    step_id: str
    ok: bool
    data: Any = None
    error: str | None = None
    latency_ms: int = 0
    cost_usd: float = 0.0
    agent: str | None = None
    tool: str | None = None


class SpanRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")

    trace_id: str
    span_id: str
    parent_span_id: str | None = None
    name: str
    start_unix_nano: int = 0
    end_unix_nano: int = 0
    status: Literal["UNSET", "OK", "ERROR"] = "UNSET"
    attributes: dict[str, Any] = Field(default_factory=dict)


class HitlPending(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ticket_id: str
    tool: str
    amount: float | None = None
    currency: str | None = None
    rationale: str = ""
    requested_at: str | None = None


class AgentState(BaseModel):
    """Public, versioned graph state.

    v1 fields remain required-with-defaults. v2 fields are optional so a v1
    checkpoint can still deserialize on a v2 worker.
    """

    model_config = ConfigDict(extra="ignore")

    schema_version: int = AGENT_STATE_SCHEMA_VERSION
    run_id: str = Field(default_factory=lambda: uuid4().hex)
    goal: str = ""
    profile: ContextProfile = Field(default_factory=ContextProfile)
    steps: list[StepResult] = Field(default_factory=list)
    spent_usd: float = 0.0
    status: RunStatus = "running"
    stop_reason: str | None = None
    checkpoint: dict[str, Any] = Field(default_factory=dict)
    # v2 — optional, defaulted
    http_status: int | None = None
    oauth_scopes: list[str] = Field(default_factory=list)
    oauth_client_id: str | None = None
    hitl_pending: HitlPending | None = None
    traces: list[SpanRecord] = Field(default_factory=list)
    fallback_queue: list[dict[str, Any]] = Field(default_factory=list)
    circuit_states: dict[str, str] = Field(default_factory=dict)
    partial_artifacts: list[str] = Field(default_factory=list)
    routed_agent: str | None = None


# Back-compat alias used by src.bridge
BridgeState = AgentState
