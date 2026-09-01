"""Planner / worker / verifier path with connectivity + budget + fault awareness."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from agentbridge.compliance.oscal_exporter import Finding, export_oscal_results
from agentbridge.core.budget_guardian import BudgetGuardian
from agentbridge.core.hitl import HitlGate
from agentbridge.core.policy import decide
from agentbridge.core.state import BridgeState, ContextProfile, StepResult
from agentbridge.core.telemetry import Tracer
from agentbridge.tools.payment_adapter import clear_fault, execute, quote, set_fault
from agentbridge.tools.payment_mcp import EXECUTE_PAYMENT, QUOTE_PAYMENT


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
        tool=QUOTE_PAYMENT.name,
        agent="planner",
    )


def verify_quote(step: StepResult, *, inject_fault: str | None = None) -> str | None:
    """Return stop_reason if verification fails, else None."""
    if inject_fault == "verifier_reject" or not (step.data or {}).get("quote_id"):
        return "verifier_reject: missing quote_id"
    return None


def _emit_oscal(state: BridgeState) -> None:
    success = state.status == "success"
    requested_amount = float(state.checkpoint.get("requested_amount", 0.0))
    payment_limit_satisfied = requested_amount <= state.profile.aml_daily_limit
    findings = [
        Finding(
            control_id="AB-BUDGET-1",
            title="Run cost ceiling",
            success=state.status != "budget_exceeded",
            rationale=state.stop_reason or "within cap",
            severity="high",
            related_task="Raise max_run_cost_usd or reduce tool fan-out",
        ),
        Finding(
            control_id="AB-AML-1",
            title="AML / payment-limit policy",
            success=payment_limit_satisfied,
            rationale=(
                f"amount={requested_amount} within aml_daily_limit={state.profile.aml_daily_limit}"
                if payment_limit_satisfied
                else f"amount={requested_amount} exceeds aml_daily_limit={state.profile.aml_daily_limit}"
            ),
            severity="high",
            related_task="Review the transaction, customer risk, and configured AML limit",
        ),
        Finding(
            control_id="AB-RUN-1",
            title="Run completion",
            success=success,
            rationale=state.stop_reason or "completed",
        ),
    ]
    try:
        paths = export_oscal_results(state.run_id, findings)
        state.partial_artifacts = list(paths.values())
    except Exception as exc:
        # Evidence failures must be visible to callers even though they should
        # not replace the original terminal status.
        state.partial_artifacts.append(f"oscal_export_failed:{exc}")


def run_quote_goal(
    goal: str,
    profile: ContextProfile | None = None,
    *,
    amount: float = 5000.0,
    inject_fault: str | None = None,
    execute_payment: bool = False,
    hitl_approved: bool = False,
) -> BridgeState:
    # Runtime guards are run-scoped. Reusing mutable guardian, tracer, or HITL
    # instances across requests leaks audit/accounting state between tenants.
    guardian = BudgetGuardian(emit_oscal=True)
    tracer = Tracer()
    hitl = HitlGate()

    clear_fault()
    state = BridgeState(goal=goal, profile=profile or ContextProfile())
    state.checkpoint["requested_amount"] = amount
    span = tracer.start("run_quote_goal", attributes={"goal": goal, "locale": state.profile.locale})

    if amount > state.profile.aml_daily_limit:
        state.status = "failed"
        state.stop_reason = (
            f"payment_limit_exceeded: amount={amount} > aml_daily_limit={state.profile.aml_daily_limit}"
        )
        _emit_oscal(state)
        tracer.end(span, status="ERROR", attributes={"control": "AB-AML-1"})
        state.traces.append(span.to_record())
        return state

    gate, reason = decide("quote", state.profile, destructive=False)
    if gate == "block":
        state.status = "failed"
        state.stop_reason = reason
        _emit_oscal(state)
        tracer.end(span, status="ERROR")
        state.traces.append(span.to_record())
        return state

    if inject_fault == "tool_timeout":
        set_fault(timeout=True)
    elif inject_fault == "provider_error":
        set_fault(error="provider_unavailable")

    planned = plan(goal, state.profile, execute_payment=execute_payment)
    state.checkpoint["plan"] = planned

    rail = planned[0]["rail"]
    step = worker_quote(state, rail, amount)
    state = guardian.charge(state, step.cost_usd, category="processing")
    if state.status == "budget_exceeded":
        clear_fault()
        tracer.end(span, status="ERROR", attributes={"http_status": 402})
        state.traces.append(span.to_record())
        return state

    state.steps.append(step)

    if not step.ok:
        state.status = "failed"
        state.stop_reason = f"tool_error: {step.error}"
        clear_fault()
        _emit_oscal(state)
        tracer.end(span, status="ERROR")
        state.traces.append(span.to_record())
        return state

    reject = verify_quote(step, inject_fault=inject_fault)
    if reject:
        state.status = "failed"
        state.stop_reason = reject
        clear_fault()
        _emit_oscal(state)
        tracer.end(span, status="ERROR")
        state.traces.append(span.to_record())
        return state

    if execute_payment:
        ticket = hitl.evaluate(
            EXECUTE_PAYMENT,
            amount=amount,
            currency=state.profile.currency,
            threshold=state.profile.hitl_amount_threshold,
        )
        if ticket is not None and not hitl_approved:
            state.hitl_pending = ticket.to_pending()
            state.status = "awaiting_hitl"
            state.stop_reason = ticket.rationale
            clear_fault()
            tracer.end(span, status="UNSET", attributes={"hitl": ticket.ticket_id})
            state.traces.append(span.to_record())
            return state

        key = uuid4().hex
        gate, reason = decide("execute", state.profile, has_idempotency=bool(key), destructive=True)
        if gate == "block":
            state.status = "failed"
            state.stop_reason = reason
            clear_fault()
            _emit_oscal(state)
            tracer.end(span, status="ERROR")
            state.traces.append(span.to_record())
            return state
        if gate == "escalate" and not hitl_approved:
            ticket = hitl.request(
                EXECUTE_PAYMENT,
                amount=amount,
                currency=state.profile.currency,
                rationale=reason,
            )
            state.hitl_pending = ticket.to_pending()
            state.status = "awaiting_hitl"
            state.stop_reason = reason
            clear_fault()
            tracer.end(span, status="UNSET", attributes={"hitl": ticket.ticket_id})
            state.traces.append(span.to_record())
            return state
        ex = execute(rail, step.data["quote_id"], key)  # type: ignore[arg-type]
        state = guardian.charge(state, float(ex.cost_estimate or 0.0), category="processing")
        if state.status == "budget_exceeded":
            clear_fault()
            tracer.end(span, status="ERROR", attributes={"http_status": 402})
            state.traces.append(span.to_record())
            return state
        state.steps.append(
            StepResult(
                step_id="execute",
                ok=bool(ex.ok),
                data=ex.data,
                error=ex.error_code,
                latency_ms=int(ex.latency_ms or 0),
                cost_usd=float(ex.cost_estimate or 0.0),
                tool=EXECUTE_PAYMENT.name,
                agent="treasurer",
            )
        )
        if not ex.ok:
            state.status = "failed"
            state.stop_reason = f"execute_error: {ex.error_code}"
            clear_fault()
            _emit_oscal(state)
            tracer.end(span, status="ERROR")
            state.traces.append(span.to_record())
            return state

    state.status = "success"
    state.checkpoint["last_quote_id"] = step.data["quote_id"]
    clear_fault()
    _emit_oscal(state)
    tracer.end(span, status="OK")
    state.traces.append(span.to_record())
    return state


def run_budget_exhaustion(profile: ContextProfile | None = None) -> BridgeState:
    guardian = BudgetGuardian(emit_oscal=True)
    p = profile or ContextProfile()
    p = p.model_copy(update={"max_run_cost_usd": 0.0015})
    state = BridgeState(goal="force budget", profile=p)
    for _ in range(5):
        state = guardian.charge(state, 0.001)
        if state.status == "budget_exceeded":
            return state
    state.status = "failed"
    state.stop_reason = "budget_not_triggered"
    return state
