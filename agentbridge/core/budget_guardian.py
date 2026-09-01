"""HTTP 402 hard-stop budget interceptor.

Wraps execution graphs. When cumulative spend crosses ``max_run_cost_usd``
the guardian:

1. Sets ``status = budget_exceeded`` and ``http_status = 402``.
2. Halts further tool invocations.
3. Commits intermediate evidence to the OSCAL audit pack (partial artifacts).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from agentbridge.core.state import AgentState

HTTP_402_PAYMENT_REQUIRED = 402


class PaymentRequiredStop(Exception):
    """Hard stop: the run has exhausted its cost ceiling."""

    status_code = HTTP_402_PAYMENT_REQUIRED

    def __init__(self, state: AgentState, message: str | None = None) -> None:
        self.state = state
        super().__init__(message or state.stop_reason or "Payment Required")


OnCapCallback = Callable[[AgentState], None]


@dataclass
class BudgetGuardian:
    """Per-run cost ceiling. Soft warnings are forbidden."""

    on_cap: OnCapCallback | None = None
    emit_oscal: bool = True
    _charged: list[float] = field(default_factory=list)

    def remaining(self, state: AgentState) -> float:
        return round(state.profile.max_run_cost_usd - state.spent_usd, 6)

    def would_exceed(self, state: AgentState, add_cost: float) -> bool:
        return round(state.spent_usd + add_cost, 6) > state.profile.max_run_cost_usd

    def charge(self, state: AgentState, add_cost: float = 0.0, *, raise_on_cap: bool = False) -> AgentState:
        if state.status == "budget_exceeded":
            state.http_status = HTTP_402_PAYMENT_REQUIRED
            if raise_on_cap:
                raise PaymentRequiredStop(state)
            return state

        projected = round(state.spent_usd + add_cost, 6)
        state.spent_usd = projected
        self._charged.append(add_cost)

        if projected > state.profile.max_run_cost_usd:
            return self._trip(state, raise_on_cap=raise_on_cap)
        return state

    def intercept(self, state: AgentState, *, raise_on_cap: bool = False) -> AgentState:
        """Pre-tool gate: refuse the next invocation if the ceiling is already blown."""
        if state.status == "budget_exceeded" or state.spent_usd > state.profile.max_run_cost_usd:
            return self._trip(state, raise_on_cap=raise_on_cap)
        return state

    def _trip(self, state: AgentState, *, raise_on_cap: bool) -> AgentState:
        state.status = "budget_exceeded"
        state.http_status = HTTP_402_PAYMENT_REQUIRED
        state.stop_reason = (
            f"spent_usd={state.spent_usd} > max_run_cost_usd={state.profile.max_run_cost_usd}"
        )
        self._commit_partial_oscal(state)
        if self.on_cap is not None:
            self.on_cap(state)
        if raise_on_cap:
            raise PaymentRequiredStop(state)
        return state

    def _commit_partial_oscal(self, state: AgentState) -> None:
        if not self.emit_oscal:
            return
        try:
            from agentbridge.compliance.oscal_exporter import Finding, export_oscal_results

            finding = Finding(
                control_id="AB-BUDGET-1",
                title="Run cost ceiling",
                success=False,
                rationale=state.stop_reason or "budget ceiling exceeded",
                severity="high",
                related_task="Raise max_run_cost_usd or reduce tool fan-out",
            )
            paths = export_oscal_results(state.run_id, [finding], extra_props={"partial": True})
            state.partial_artifacts = list(paths.values())
        except Exception as exc:  # never mask the 402 with exporter errors
            state.partial_artifacts.append(f"oscal_export_failed:{exc}")


_DEFAULT = BudgetGuardian()


def guard(state: AgentState, add_cost: float = 0.0) -> AgentState:
    """Back-compat wrapper used by ``src.bridge.budget``."""
    return _DEFAULT.charge(state, add_cost)


def payment_required_body(state: AgentState) -> dict[str, Any]:
    return {
        "error": "payment_required",
        "status": HTTP_402_PAYMENT_REQUIRED,
        "run_id": state.run_id,
        "spent_usd": state.spent_usd,
        "max_run_cost_usd": state.profile.max_run_cost_usd,
        "stop_reason": state.stop_reason,
        "partial_artifacts": state.partial_artifacts,
    }
