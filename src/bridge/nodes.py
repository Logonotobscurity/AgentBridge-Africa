"""Planner / worker / verifier path with connectivity + budget + fault awareness."""
from __future__ import annotations

from typing import Any
from uuid import uuid4

from src.bridge.budget import guard
from src.bridge.policy_gate import decide
from src.bridge.state import BridgeState, ContextProfile, StepResult
from tools.payments_adapter import clear_fault, execute, quote, set_fault


def plan(goal: str, profile: ContextProfile, *, execute_payment: bool = False) -> list[dict[str, Any]]:
    """Deterministic planner: goal → ordered tool steps. ContextProfile is code, not prompt."""
    rail = profile.payment_rails[0] if profile.payment_rails else "bank"
    steps: list[dict[str, Any]] = [
        {"id": "quote", "tool": "quote", "rail": rail, "goal": goal},
    ]
    if execute_payment:
        steps.append({"id": "execute", "tool": "execute", "rail": rail})
    return steps


def _country(currency: str) -> str:
    return "NG" if currency == "NGN" else "KE" if currency == "KES" else "XX"


def worker_quote(state: BridgeState, rail: str, amount: float) -> StepResult:
    env = quote(rail, amount, state.profile.currency, country=_country(state.profile.currency))  # type: ignore[arg-type]
    cost = float(env.cost_estimate or 0.0)
    return StepResult(
        step_id="quote",
        ok=bool(env.ok),
        data=env.data,
        error=env.error_code,
        latency_ms=int(env.latency_ms or 0),
        cost_usd=cost,
    )


def verify_quote(step: StepResult, *, inject_fault: str | None = None) -> str | None:
    """Return stop_reason if verification fails, else None."""
    if inject_fault == "verifier_reject" or not (step.data or {}).get("quote_id"):
        return "verifier_reject: missing quote_id"
    return None


def run_quote_goal(
    goal: str,
    profile: ContextProfile | None = None,
    *,
    amount: float = 5000.0,
    inject_fault: str | None = None,
    execute_payment: bool = False,
) -> BridgeState:
    clear_fault()
    state = BridgeState(goal=goal, profile=profile or ContextProfile())

    gate, reason = decide("quote", state.profile, destructive=False)
    if gate == "block":
        state.status = "failed"
        state.stop_reason = reason
        return state

    if inject_fault == "tool_timeout":
        set_fault(timeout=True)
    elif inject_fault == "provider_error":
        set_fault(error="provider_unavailable")

    planned = plan(goal, state.profile, execute_payment=execute_payment)
    state.checkpoint["plan"] = planned

    rail = planned[0]["rail"]
    step = worker_quote(state, rail, amount)
    state = guard(state, step.cost_usd)
    if state.status == "budget_exceeded":
        clear_fault()
        return state

    state.steps.append(step)

    if not step.ok:
        state.status = "failed"
        state.stop_reason = f"tool_error: {step.error}"
        clear_fault()
        return state

    reject = verify_quote(step, inject_fault=inject_fault)
    if reject:
        state.status = "failed"
        state.stop_reason = reject
        clear_fault()
        return state

    if execute_payment:
        key = uuid4().hex
        gate, reason = decide("execute", state.profile, has_idempotency=bool(key), destructive=True)
        if gate == "block":
            state.status = "failed"
            state.stop_reason = reason
            clear_fault()
            return state
        ex = execute(rail, step.data["quote_id"], key)  # type: ignore[arg-type]
        state = guard(state, float(ex.cost_estimate or 0.0))
        if state.status == "budget_exceeded":
            clear_fault()
            return state
        state.steps.append(
            StepResult(
                step_id="execute",
                ok=bool(ex.ok),
                data=ex.data,
                error=ex.error_code,
                latency_ms=int(ex.latency_ms or 0),
                cost_usd=float(ex.cost_estimate or 0.0),
            )
        )
        if not ex.ok:
            state.status = "failed"
            state.stop_reason = f"execute_error: {ex.error_code}"
            clear_fault()
            return state

    state.status = "success"
    state.checkpoint["last_quote_id"] = step.data["quote_id"]
    clear_fault()
    return state


def run_budget_exhaustion(profile: ContextProfile | None = None) -> BridgeState:
    p = profile or ContextProfile()
    p = p.model_copy(update={"max_run_cost_usd": 0.0015})
    state = BridgeState(goal="force budget", profile=p)
    for _ in range(5):
        state = guard(state, 0.001)
        if state.status == "budget_exceeded":
            return state
    state.status = "failed"
    state.stop_reason = "budget_not_triggered"
    return state
