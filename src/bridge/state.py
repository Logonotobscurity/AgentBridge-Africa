"""AgentBridge run state."""
from __future__ import annotations

from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


class ContextProfile(BaseModel):
    locale: str = "en-NG"
    currency: str = "NGN"
    id_formats: list[str] = Field(default_factory=lambda: ["NIN", "BVN"])
    payment_rails: list[str] = Field(default_factory=lambda: ["bank", "ussd", "mobile_money"])
    connectivity: Literal["stable", "intermittent", "offline_first"] = "intermittent"
    max_tool_latency_ms: int = 8000
    max_run_cost_usd: float = 0.15
    language_preference: str = "en"


class StepResult(BaseModel):
    step_id: str
    ok: bool
    data: Any = None
    error: str | None = None
    latency_ms: int = 0
    cost_usd: float = 0.0


class BridgeState(BaseModel):
    run_id: str = Field(default_factory=lambda: uuid4().hex)
    goal: str = ""
    profile: ContextProfile = Field(default_factory=ContextProfile)
    steps: list[StepResult] = Field(default_factory=list)
    spent_usd: float = 0.0
    status: Literal["running", "success", "failed", "budget_exceeded"] = "running"
    stop_reason: str | None = None
    checkpoint: dict[str, Any] = Field(default_factory=dict)
