"""Unified payment facade that keeps provider selection out of agent prompts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from agentbridge.core.rail_switch import RailRouter, RailSelection
from agentbridge.core.state import ContextProfile
from agentbridge.tools.payment_adapter import ToolEnvelope, execute, quote, status


@dataclass
class UnifiedPaymentEngine:
    router: RailRouter = field(default_factory=RailRouter)

    def quote(
        self,
        profile: ContextProfile,
        *,
        amount: float,
        currency: str,
        destination_country: str,
        availability: Mapping[str, bool] | None = None,
    ) -> tuple[RailSelection, ToolEnvelope]:
        selection = self.router.select(
            profile,
            currency=currency,
            destination_country=destination_country,
            availability=availability,
        )
        envelope = quote(
            selection.rail,  # type: ignore[arg-type]
            amount,
            currency,
            country=destination_country,
        )
        return selection, envelope

    def execute(
        self,
        selection: RailSelection,
        *,
        quote_id: str,
        idempotency_key: str,
    ) -> ToolEnvelope:
        """Execute on the rail pinned when the intent was created.

        A provider must never be switched underneath an existing provider
        reference. Failover creates a new intent and idempotency key instead.
        """
        return execute(selection.rail, quote_id, idempotency_key)  # type: ignore[arg-type]

    def status(self, transaction_id: str) -> ToolEnvelope:
        return status(transaction_id)
