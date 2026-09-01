"""Golden trajectory harness with failure injection."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agentbridge.core.orchestrator import run_budget_exhaustion, run_quote_goal  # noqa: E402
from agentbridge.core.state import ContextProfile  # noqa: E402


def load_profile(rel: str) -> ContextProfile:
    data = json.loads((ROOT / rel).read_text())
    return ContextProfile(**data)


def _p50(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


def run_one(spec: dict) -> dict:
    profile = load_profile(spec["profile"])
    fault = spec.get("inject_fault")
    if fault in (None, "null", ""):
        fault = None

    if fault == "budget" or str(spec.get("id", "")).startswith("pay_budget"):
        state = run_budget_exhaustion(profile)
        ok = state.status == "budget_exceeded"
        latency = sum(s.latency_ms for s in state.steps)
        return {
            "id": spec["id"],
            "ok": ok,
            "status": state.status,
            "spent_usd": state.spent_usd,
            "steps": len(state.steps),
            "latency_ms": latency,
            "stop_reason": state.stop_reason,
        }

    want_success = bool(spec.get("expected", {}).get("success", True))
    state = run_quote_goal(spec["goal"], profile, inject_fault=fault)
    ok = (state.status == "success") if want_success else (state.status != "success")
    latency = sum(s.latency_ms for s in state.steps)
    return {
        "id": spec["id"],
        "ok": ok,
        "status": state.status,
        "spent_usd": state.spent_usd,
        "steps": len(state.steps),
        "latency_ms": latency,
        "stop_reason": state.stop_reason,
    }


def run_all() -> dict:
    traj_dir = ROOT / "evals" / "trajectories"
    results = [run_one(yaml.safe_load(p.read_text())) for p in sorted(traj_dir.glob("*.yaml"))]
    n = len(results) or 1
    success_rate = round(100.0 * sum(1 for r in results if r["ok"]) / n, 2)
    costs = sorted(r["spent_usd"] for r in results)
    success_costs = sorted(r["spent_usd"] for r in results if r["status"] == "success")
    out = {
        "n": len(results),
        "success_rate_pct": success_rate,
        "median_cost_usd": _p50(costs),
        "median_cost_per_success_usd": _p50(success_costs),
        "p50_latency_ms": _p50([float(r["latency_ms"]) for r in results]),
        "results": results,
        "notes": "Trajectories: happy NG/KE, timeout, provider error, verifier, offline, budget",
    }
    out_path = ROOT / "evals" / "results" / "latest.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2))
    return out


if __name__ == "__main__":
    summary = run_all()
    print(json.dumps(summary, indent=2))
    raise SystemExit(0 if summary["success_rate_pct"] == 100 else 1)
