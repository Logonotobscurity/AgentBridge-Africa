"""Production provider-neutral async payment engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from agentbridge.core.rail_switch import RailRouter, RailSelection
from agentbridge.core.state import ContextProfile
from agentbridge.payments.models import ConnectorResult, PaymentIntent
from agentbridge.payments.registry import CapabilityPackRegistry

_ADAPTER_BY_RAIL = {
    "mpesa": "ke-payments",
    "paystack": "ng-payments",
    "mtn_momo": "gh-payments",
}


@dataclass
class ProductionPaymentEngine:
    registry: CapabilityPackRegistry
    router: RailRouter = field(default_factory=RailRouter)

    async def initiate(
        self,
        profile: ContextProfile,
        intent: PaymentIntent,
        *,
        availability: Mapping[str, bool] | None = None,
    ) -> tuple[RailSelection, ConnectorResult]:
        selection = self.router.select(
            profile,
            currency=intent.currency.value,
            destination_country=intent.destination_country.value,
            availability=availability,
        )
        try:
            adapter_id = _ADAPTER_BY_RAIL[selection.rail]
        except KeyError as exc:
            raise LookupError(f"selected rail has no live capability pack: {selection.rail}") from exc
        if selection.rail == "mtn_momo" and intent.destination_country.value == "UG":
            adapter_id = "ug-payments"
        adapter = self.registry.resolve(adapter_id)
        return selection, await adapter.initiate_payment(intent)

    async def query(self, adapter_id: str, provider_reference: str) -> ConnectorResult:
        """Query the capability pack pinned in durable payment state."""
        adapter = self.registry.resolve(adapter_id)
        return await adapter.query_transaction_status(provider_reference)
