"""Paystack transaction capability pack."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Mapping
from urllib.parse import quote as url_quote

from agentbridge.payments.base import ambiguous_http_status, uncertain
from agentbridge.payments.models import ConnectorResult, Country, Currency, PaymentIntent
from agentbridge.payments.runtime import (
    ALLOWLIST_ENDPOINTS,
    DEFAULT_LIMITS,
    AsyncTransport,
    Environment,
    HttpxTransport,
    PaymentLimit,
    RecipientResolver,
    RuntimeSecretsManager,
    SecretResolver,
    TransportError,
    validate_limit,
)

_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def _minor_units(amount: Decimal) -> int:
    quantized = amount.quantize(Decimal("0.01"))
    if quantized != amount:
        raise ValueError("Paystack amount supports at most two decimal places")
    return int(quantized * 100)


@dataclass
class PaystackConnector:
    recipient_resolver: RecipientResolver
    environment: Environment = Environment.PRODUCTION
    transport: AsyncTransport = field(default_factory=HttpxTransport)
    secrets: SecretResolver = field(default_factory=RuntimeSecretsManager)
    limits: Mapping[tuple[str, Currency], PaymentLimit] = field(default_factory=lambda: dict(DEFAULT_LIMITS))
    timeout_seconds: float = 10.0
    provider: str = field(default="paystack", init=False)

    @property
    def base_url(self) -> str:
        return ALLOWLIST_ENDPOINTS[self.provider][self.environment]

    def _headers(self) -> dict[str, str]:
        secret = self.secrets.resolve(self.provider, ("secret_key",)).reveal("secret_key")
        return {"Authorization": f"Bearer {secret}", "Content-Type": "application/json"}

    async def initiate_payment(self, intent: PaymentIntent) -> ConnectorResult:
        if intent.destination_country is not Country.NG or intent.currency is not Currency.NGN:
            raise ValueError("Paystack connector currently supports NG/NGN intents")
        validate_limit(self.provider, intent, self.limits)
        recipient = self.recipient_resolver.resolve(intent.recipient_ref)
        if not _EMAIL.fullmatch(recipient):
            raise ValueError("Paystack collection recipient must be a valid email address")
        reference = intent.merchant_reference
        try:
            response = await self.transport.request(
                "POST",
                f"{self.base_url}/transaction/initialize",
                headers=self._headers(),
                json={
                    "amount": _minor_units(intent.amount),
                    "email": recipient,
                    "currency": intent.currency.value,
                    "reference": reference,
                    "metadata": {"intent_id": str(intent.intent_id)},
                },
                timeout=self.timeout_seconds,
            )
        except TransportError:
            return uncertain(self.provider, reference, "TRANSPORT_UNCERTAIN")
        data = response.data.get("data") if isinstance(response.data.get("data"), Mapping) else {}
        provider_reference = str(data.get("reference") or reference)
        if ambiguous_http_status(response.status_code):
            return uncertain(self.provider, provider_reference, "PROVIDER_UNAVAILABLE")
        if response.status_code in {200, 201} and response.data.get("status") is True:
            return ConnectorResult(
                provider=self.provider,
                status="SUBMITTED",
                provider_reference=provider_reference,
                provider_code="initialized",
                requires_reconciliation=True,
            )
        return ConnectorResult(
            provider=self.provider,
            status="FAILED",
            provider_reference=provider_reference,
            provider_code=str(response.status_code),
            error_code="INITIATION_REJECTED",
        )

    async def query_transaction_status(self, provider_reference: str) -> ConnectorResult:
        encoded_reference = url_quote(provider_reference, safe="")
        try:
            response = await self.transport.request(
                "GET",
                f"{self.base_url}/transaction/verify/{encoded_reference}",
                headers=self._headers(),
                timeout=self.timeout_seconds,
            )
        except TransportError:
            return uncertain(self.provider, provider_reference, "QUERY_UNAVAILABLE")
        data = response.data.get("data") if isinstance(response.data.get("data"), Mapping) else {}
        status = str(data.get("status") or "").lower()
        if response.status_code == 200 and status == "success":
            return ConnectorResult(
                provider=self.provider,
                status="CONFIRMED",
                provider_reference=provider_reference,
                transaction_id=str(data.get("id") or "") or None,
                provider_code=status,
            )
        if status in {"failed", "abandoned", "reversed"}:
            return ConnectorResult(
                provider=self.provider,
                status="FAILED",
                provider_reference=provider_reference,
                provider_code=status,
                error_code="PROVIDER_TERMINAL_FAILURE",
            )
        return uncertain(self.provider, provider_reference, "STATUS_PENDING")
