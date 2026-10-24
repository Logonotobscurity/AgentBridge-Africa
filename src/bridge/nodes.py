"""Bridge nodes — tool call, budget guard, status."""
from __future__ import annotations

from src.bridge.budget import guard
from src.bridge.state import BridgeState


def run_trajectory(spec: dict) -> dict:
    # Minimal deterministic runner for golden evals
    state = BridgeState(run_id=spec["id"], profile={"locale": "en-NG"})
    # Simulate success unless fault injected
    fault = spec.get("inject_fault")
    if fault:
        state.status = "error"
        state.stop_reason = fault
        return {"success": False, "status": state.status, "reason": fault}
    state.status = "done"
    return {"success": True, "status": state.status}
