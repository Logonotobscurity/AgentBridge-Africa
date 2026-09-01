"""Multi-agent A2A routing layer.

Routes a tool invocation to the least-privilege specialist agent, applying
annotation gates, OAuth scopes, circuit state, and HITL before dispatch.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from agentbridge.core.budget_guardian import BudgetGuardian, payment_required_body
from agentbridge.core.circuit_breaker import CircuitBreaker, CircuitOpenError
from agentbridge.core.hitl import ConfirmationEvidence, HitlGate
from agentbridge.core.oauth import TokenClaims, scope_allows
from agentbridge.core.state import AgentState
from agentbridge.core.telemetry import Tracer
from agentbridge.core.timeouts import TimeoutBudget, TimeoutError, call_with_timeout
from agentbridge.tools.mcp_types import Tool, annotations_dict
from agentbridge.tools.payment_mcp import require_idempotency

Decision = Literal["dispatch", "block", "escalate", "hitl", "degrade"]
ConfirmationValidator = Callable[[ConfirmationEvidence, AgentState, Any], bool]

AGENT_ROLES = {
    "planner": {"payments:quote", "payments:status", "compliance:read"},
    "worker": {"payments:quote", "payments:status", "payments:execute"},
    "verifier": {"payments:status", "compliance:read"},
    "treasurer": {"payments:execute", "payments:quote"},
    "compliance_officer": {"compliance:read", "compliance:write"},
    "fallback": {"payments:quote", "payments:status"},
}


@dataclass
class RouteResult:
    decision: Decision
    agent: str
    reason: str
    tool: str
    annotations: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentRouter:
    hitl: HitlGate = field(default_factory=HitlGate)
    budget: BudgetGuardian = field(default_factory=BudgetGuardian)
    tracer: Tracer = field(default_factory=Tracer)
    breakers: dict[str, CircuitBreaker] = field(default_factory=dict)
    timeout: TimeoutBudget = field(default_factory=TimeoutBudget)
    fallback_handler: Callable[[AgentState, str, dict[str, Any]], Any] | None = None
    confirmation_validator: ConfirmationValidator | None = None

    def breaker_for(self, provider: str) -> CircuitBreaker:
        if provider not in self.breakers:
            self.breakers[provider] = CircuitBreaker(name=provider)
        return self.breakers[provider]

    def select_agent(self, tool: Tool | str, *, destructive: bool | None = None) -> str:
        name = tool if isinstance(tool, str) else tool.name
        anns = annotations_dict(tool) if not isinstance(tool, str) else {}
        is_destructive = destructive if destructive is not None else bool(anns.get("destructive"))
        if name.startswith("oscal") or "compliance" in name:
            return "compliance_officer"
        if is_destructive or name in {"mpesa_stk_push", "execute", "execute_payment", "bank_transfer_ngn"}:
            return "treasurer"
        if name in {"mpesa_stk_query", "status", "paystack_verify"}:
            return "verifier"
        if name in {"quote", "quote_payment"}:
            return "planner"
        return "worker"

    def gate(
        self,
        state: AgentState,
        tool: Tool,
        arguments: dict[str, Any] | None = None,
        *,
        token: TokenClaims | None = None,
        confirmation: ConfirmationEvidence | None = None,
    ) -> RouteResult:
        anns = annotations_dict(tool)
        agent = self.select_agent(tool)
        arguments = arguments or {}

        # A destructive primitive without a stable request key is unsafe to
        # retry and must never reach a provider. Keep this in the central
        # router rather than relying on each adapter to remember the check.
        try:
            require_idempotency(tool.name, arguments)
        except ValueError as exc:
            return RouteResult("block", agent, str(exc), tool.name, anns)

        if token is not None:
            needed = "payments:execute" if anns.get("destructive") else "payments:quote"
            if anns.get("readOnly"):
                needed = "payments:status" if "query" in tool.name or tool.name == "status" else "payments:quote"
            if not scope_allows(token, needed):
                return RouteResult("block", agent, f"token missing scope {needed}", tool.name, anns)

        if state.profile.connectivity == "offline_first":
            reason = (
                "offline_first: serve cached read path"
                if anns.get("readOnly")
                else "offline_first: queue side-effect"
            )
            return RouteResult("degrade", "fallback", reason, tool.name, anns)

        amount = _coerce_amount(arguments)
        if anns.get("destructive") and confirmation is not None:
            if self.confirmation_validator is None:
                return RouteResult(
                    "block", agent, "confirmation verifier is not configured", tool.name, anns
                )
            if not self.confirmation_validator(confirmation, state, tool):
                return RouteResult("block", agent, "confirmation evidence rejected", tool.name, anns)
            state.confirmations.append(
                {
                    "tool": tool.name,
                    "method": confirmation.method,
                    "reference": confirmation.reference,
                    "subject": confirmation.subject,
                }
            )
            ticket = None
        else:
            ticket = self.hitl.evaluate(
                tool,
                amount=amount,
                currency=state.profile.currency,
                threshold=state.profile.hitl_amount_threshold,
            )
        if ticket is not None:
            state.hitl_pending = ticket.to_pending()
            state.status = "awaiting_hitl"
            return RouteResult("hitl", agent, ticket.rationale, tool.name, anns)

        return RouteResult("dispatch", agent, f"routed to {agent}", tool.name, anns)

    def dispatch(
        self,
        state: AgentState,
        tool: Tool,
        fn: Callable[..., Any],
        arguments: dict[str, Any] | None = None,
        *,
        provider: str = "default",
        token: TokenClaims | None = None,
        confirmation: ConfirmationEvidence | None = None,
    ) -> tuple[AgentState, Any]:
        arguments = arguments or {}

        # This is the final pre-provider interceptor. A resumed graph whose
        # budget was previously exhausted must return the same HTTP 402 stop
        # signal without evaluating policy or invoking another tool.
        self.budget.intercept(state)
        if state.status == "budget_exceeded":
            return state, payment_required_body(state)

        route = self.gate(state, tool, arguments, token=token, confirmation=confirmation)
        state.routed_agent = route.agent
        state.circuit_states = {k: v.state for k, v in self.breakers.items()}

        if route.decision == "block":
            state.status = "failed"
            state.stop_reason = route.reason
            return state, None
        if route.decision == "hitl":
            return state, None
        if route.decision == "degrade":
            state.status = "degraded"
            if self.fallback_handler is not None:
                return state, self.fallback_handler(state, tool.name, arguments)
            if route.annotations.get("readOnly"):
                return state, {"cached": False, "tool": tool.name, "reason": route.reason}
            state.fallback_queue.append({"tool": tool.name, "arguments": arguments})
            return state, {"queued": True, "tool": tool.name, "reason": route.reason}

        breaker = self.breaker_for(provider)
        span = self.tracer.start(tool.name, attributes={"agent": route.agent, "provider": provider})
        try:
            result = breaker.call(lambda: call_with_timeout(fn, self.timeout.ms_for(tool.name), **arguments))
            self.tracer.end(span, status="OK")
            state.traces.append(span.to_record())
            return state, result
        except CircuitOpenError as exc:
            self.tracer.end(span, status="ERROR", attributes={"error": str(exc)})
            state.traces.append(span.to_record())
            state.fallback_queue.append({"tool": tool.name, "arguments": arguments, "reason": "circuit_open"})
            state.status = "degraded"
            state.stop_reason = str(exc)
            if self.fallback_handler is not None:
                return state, self.fallback_handler(state, tool.name, arguments)
            return state, None
        except TimeoutError as exc:
            self.tracer.end(span, status="ERROR", attributes={"error": "timeout"})
            state.traces.append(span.to_record())
            state.status = "failed"
            state.stop_reason = f"tool_timeout:{tool.name}"
            raise exc


def _coerce_amount(arguments: dict[str, Any]) -> float | None:
    raw = arguments.get("amount")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None
