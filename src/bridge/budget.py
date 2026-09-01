"""Hard budget stop — do not soft-fail into infinite retries."""
from __future__ import annotations

from src.bridge.state import BridgeState


def guard(state: BridgeState, add_cost: float = 0.0) -> BridgeState:
    state.spent_usd = round(state.spent_usd + add_cost, 6)
    if state.spent_usd > state.profile.max_run_cost_usd:
        state.status = "budget_exceeded"
        state.stop_reason = (
            f"spent_usd={state.spent_usd} > max_run_cost_usd={state.profile.max_run_cost_usd}"
        )
    return state
