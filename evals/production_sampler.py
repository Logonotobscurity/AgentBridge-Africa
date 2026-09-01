"""Convert live production traces into dynamic golden-trajectory evals.

Scores model decisions against tool-argument accuracy and execution grounding.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from agentbridge.tools.payment_mcp import TOOLS_BY_NAME
from agentbridge.tools.mcp_types import annotations_dict


def score_trace(trace: dict[str, Any]) -> dict[str, Any]:
    """Score one production trace.

    ``tool_argument_accuracy`` — required inputSchema keys present.
    ``execution_grounding`` — destructive tools carried an idempotency_key
    and were not invoked under a read-only token.
    """
    tool_name = trace.get("tool")
    args = trace.get("arguments") or {}
    tool = TOOLS_BY_NAME.get(tool_name or "")
    required = []
    if tool is not None:
        required = list((tool.inputSchema or {}).get("required") or [])
    missing = [k for k in required if k not in args or args[k] in (None, "")]
    argument_accuracy = 1.0 if not required else round((len(required) - len(missing)) / len(required), 3)

    grounding = 1.0
    reasons: list[str] = []
    if tool is not None:
        anns = annotations_dict(tool)
        if anns.get("destructive") and not args.get("idempotency_key"):
            grounding = 0.0
            reasons.append("destructive_without_idempotency_key")
        scopes = set(trace.get("oauth_scopes") or [])
        if anns.get("destructive") and "payments:execute" not in scopes and scopes:
            grounding = min(grounding, 0.0)
            reasons.append("execute_without_scope")
        if anns.get("readOnly") and trace.get("mutated_ledger"):
            grounding = 0.0
            reasons.append("read_only_tool_mutated_state")

    retry_count = int(trace.get("retry_count") or 0)
    max_retries = int(trace.get("max_retries") or 3)
    retry_behavior = 1.0
    if retry_count > max_retries:
        retry_behavior = 0.0
        reasons.append("retry_budget_exceeded")

    return {
        "id": trace.get("id") or trace.get("span_id"),
        "tool": tool_name,
        "tool_argument_accuracy": argument_accuracy,
        "execution_grounding": grounding,
        "failure_retry_behavior": retry_behavior,
        "missing_args": missing,
        "reasons": reasons,
        "pass": argument_accuracy == 1.0 and grounding == 1.0 and retry_behavior == 1.0,
    }


def traces_to_yaml(traces: list[dict[str, Any]], dest: Path) -> Path:
    cases = []
    for t in traces:
        scored = score_trace(t)
        cases.append(
            {
                "id": f"prod_{scored['id']}",
                "goal": t.get("goal") or f"Replay {t.get('tool')}",
                "profile": t.get("profile") or "profiles/en-NG.json",
                "expected": {"success": bool(scored["pass"])},
                "source": "production_trace",
                "score": {
                    "tool_argument_accuracy": scored["tool_argument_accuracy"],
                    "execution_grounding": scored["execution_grounding"],
                },
            }
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(yaml.safe_dump(cases, sort_keys=False))
    return dest


def score_file(path: Path) -> dict[str, Any]:
    traces = json.loads(path.read_text())
    if isinstance(traces, dict):
        traces = traces.get("traces") or traces.get("spans") or [traces]
    scores = [score_trace(t) for t in traces]
    n = len(scores) or 1
    return {
        "n": len(scores),
        "pass_rate_pct": round(100.0 * sum(1 for s in scores if s["pass"]) / n, 2),
        "mean_argument_accuracy": round(sum(s["tool_argument_accuracy"] for s in scores) / n, 3),
        "mean_execution_grounding": round(sum(s["execution_grounding"] for s in scores) / n, 3),
        "scores": scores,
    }
