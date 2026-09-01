"""Policy gate — allow | block | escalate (comply54-shaped).

Does not execute anything. Callers must honor `block`.
"""
from __future__ import annotations

from typing import Literal

from agentbridge.core.state import ContextProfile

Decision = Literal["allow", "block", "escalate"]


def decide(
    tool: str,
    profile: ContextProfile,
    *,
    has_idempotency: bool = False,
    destructive: bool = False,
) -> tuple[Decision, str]:
    if profile.connectivity == "offline_first" and (destructive or tool in {"execute", "quote"}):
        return "block", "offline_first: remote and side-effect tools blocked"

    if tool == "execute":
        if not has_idempotency:
            return "block", "execute requires idempotency_key"
        if profile.connectivity == "intermittent":
            return "escalate", "intermittent connectivity: execute allowed with review hint"
        return "allow", "execute permitted"

    if tool in {"quote", "status"}:
        return "allow", "read-only tool permitted"

    return "escalate", f"unknown tool {tool}: default escalate"
