"""Async capability-pack protocol and safe status helpers."""

from __future__ import annotations

from typing import Protocol

from agentbridge.payments.models import ConnectorResult, PaymentIntent


class PaymentProviderAdapter(Protocol):
    provider: str

    async def initiate_payment(self, intent: PaymentIntent) -> ConnectorResult: ...

    async def query_transaction_status(self, provider_reference: str) -> ConnectorResult: ...


def ambiguous_http_status(status_code: int) -> bool:
    """Statuses where submission may have succeeded or a retry must be delayed."""
    return status_code in {408, 409, 425, 429} or status_code >= 500


def uncertain(provider: str, reference: str, code: str) -> ConnectorResult:
    """Represent ambiguous transport/server outcomes as non-terminal."""
    return ConnectorResult(
        provider=provider,
        status="PENDING",
        provider_reference=reference,
        error_code=code,
        requires_reconciliation=True,
    )
