"""Human-in-the-loop interceptors for destructive payment tools.

Tools annotated ``destructive: True`` pause when the transaction amount
exceeds the profile threshold until an operator approves.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from agentbridge.core.state import HitlPending
from agentbridge.tools.mcp_types import Tool, annotations_dict

HitlDecision = Literal["pending", "approved", "denied"]


@dataclass
class HitlTicket:
    ticket_id: str
    tool: str
    amount: float | None
    currency: str | None
    rationale: str
    requested_at: str
    status: HitlDecision = "pending"
    decided_by: str | None = None

    def to_pending(self) -> HitlPending:
        return HitlPending(
            ticket_id=self.ticket_id,
            tool=self.tool,
            amount=self.amount,
            currency=self.currency,
            rationale=self.rationale,
            requested_at=self.requested_at,
        )


@dataclass
class HitlGate:
    tickets: dict[str, HitlTicket] = field(default_factory=dict)

    def evaluate(
        self,
        tool: Tool | str,
        *,
        amount: float | None,
        currency: str | None,
        threshold: float,
    ) -> HitlTicket | None:
        name = tool if isinstance(tool, str) else tool.name
        anns = annotations_dict(tool) if not isinstance(tool, str) else {"destructive": True}
        if not anns.get("destructive"):
            return None
        if amount is None or amount < threshold:
            return None
        return self.request(
            name,
            amount=amount,
            currency=currency,
            rationale=(
                f"destructive tool {name} amount={amount} {currency or ''} "
                f"exceeds HITL threshold {threshold}"
            ).strip(),
        )

    def request(
        self,
        tool: Tool | str,
        *,
        amount: float | None,
        currency: str | None,
        rationale: str,
    ) -> HitlTicket:
        """Create an explicit ticket for a policy escalation."""
        name = tool if isinstance(tool, str) else tool.name
        ticket = HitlTicket(
            ticket_id=uuid4().hex[:12],
            tool=name,
            amount=amount,
            currency=currency,
            rationale=rationale,
            requested_at=datetime.now(timezone.utc).isoformat(),
        )
        self.tickets[ticket.ticket_id] = ticket
        return ticket

    def decide(self, ticket_id: str, decision: HitlDecision, *, operator: str = "operator") -> HitlTicket:
        ticket = self.tickets[ticket_id]
        if decision not in {"approved", "denied"}:
            raise ValueError("decision must be approved or denied")
        ticket.status = decision
        ticket.decided_by = operator
        return ticket

    def pending(self) -> list[HitlTicket]:
        return [t for t in self.tickets.values() if t.status == "pending"]


def ticket_to_dict(ticket: HitlTicket) -> dict[str, Any]:
    return {
        "ticket_id": ticket.ticket_id,
        "tool": ticket.tool,
        "amount": ticket.amount,
        "currency": ticket.currency,
        "rationale": ticket.rationale,
        "requested_at": ticket.requested_at,
        "status": ticket.status,
        "decided_by": ticket.decided_by,
    }
