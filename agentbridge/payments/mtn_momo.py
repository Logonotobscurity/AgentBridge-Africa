"""MTN MoMo Collection capability pack."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping
from uuid import NAMESPACE_URL, uuid5

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
    basic_auth,
    validate_limit,
)

_TARGET_ENVIRONMENT = {Country.GH: "ghana", Country.UG: "uganda"}


def deterministic_reference(idempotency_key_ref: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"agentbridge:mtn-momo:{idempotency_key_ref}"))


def normalize_momo_msisdn(value: str, country: Country) -> str:
    prefixes = {Country.GH: "233", Country.UG: "256"}
    prefix = prefixes[country]
    digits = "".join(character for character in value if character.isdigit())
    if digits.startswith("0"):
        digits = prefix + digits[1:]
    if len(digits) != 12 or not digits.startswith(prefix):
        raise ValueError(f"recipient must be an E.164-style {country.value} mobile number")
    return digits


@dataclass
class MtnMomoConnector:
    recipient_resolver: RecipientResolver
    market: Country = Country.GH
    environment: Environment = Environment.PRODUCTION
    transport: AsyncTransport = field(default_factory=HttpxTransport)
    secrets: SecretResolver = field(default_factory=RuntimeSecretsManager)
    limits: Mapping[tuple[str, Currency], PaymentLimit] = field(default_factory=lambda: dict(DEFAULT_LIMITS))
    timeout_seconds: float = 10.0
    provider: str = field(default="mtn_momo", init=False)

    @property
    def base_url(self) -> str:
        return ALLOWLIST_ENDPOINTS[self.provider][self.environment]

    async def _headers(self, country: Country, *, reference: str | None = None) -> dict[str, str]:
        credentials = self.secrets.resolve(
            self.provider, ("subscription_key", "api_user", "api_key")
        )
        target = "sandbox" if self.environment is Environment.SANDBOX else _TARGET_ENVIRONMENT[country]
        token_response = await self.transport.request(
            "POST",
            f"{self.base_url}/collection/token/",
            headers={
                "Authorization": basic_auth(
                    credentials.reveal("api_user"), credentials.reveal("api_key")
                ),
                "Ocp-Apim-Subscription-Key": credentials.reveal("subscription_key"),
            },
            timeout=self.timeout_seconds,
        )
        token = str(token_response.data.get("access_token") or "")
        if token_response.status_code not in {200, 201} or not token:
            raise TransportError("MTN MoMo access token unavailable")
        headers = {
            "Authorization": f"Bearer {token}",
            "Ocp-Apim-Subscription-Key": credentials.reveal("subscription_key"),
            "X-Target-Environment": target,
            "Content-Type": "application/json",
        }
        if reference:
            headers["X-Reference-Id"] = reference
        return headers

    async def initiate_payment(self, intent: PaymentIntent) -> ConnectorResult:
        expected_currency = {Country.GH: Currency.GHS, Country.UG: Currency.UGX}
        if self.market not in expected_currency:
            raise ValueError("MTN MoMo connector market must be GH or UG")
        if intent.destination_country is not self.market:
            raise ValueError("payment intent does not match the configured MTN MoMo market")
        if intent.currency is not expected_currency[self.market]:
            raise ValueError("MTN MoMo currency does not match destination corridor")
        validate_limit(self.provider, intent, self.limits)
        recipient = normalize_momo_msisdn(
            self.recipient_resolver.resolve(intent.recipient_ref), self.market
        )
        reference = deterministic_reference(intent.idempotency_key_ref)
        headers = await self._headers(intent.destination_country, reference=reference)
        try:
            response = await self.transport.request(
                "POST",
                f"{self.base_url}/collection/v1_0/requesttopay",
                headers=headers,
                json={
                    "amount": format(intent.amount, "f"),
                    "currency": intent.currency.value,
                    "externalId": intent.merchant_reference,
                    "payer": {"partyIdType": "MSISDN", "partyId": recipient},
                    "payerMessage": "AgentBridge payment",
                    "payeeNote": intent.merchant_reference,
                },
                timeout=self.timeout_seconds,
            )
        except TransportError:
            return uncertain(self.provider, reference, "TRANSPORT_UNCERTAIN")
        if response.status_code == 202:
            return ConnectorResult(
                provider=self.provider,
                status="SUBMITTED",
                provider_reference=reference,
                provider_code="202",
                requires_reconciliation=True,
            )
        if ambiguous_http_status(response.status_code):
            return uncertain(self.provider, reference, "PROVIDER_UNAVAILABLE")
        return ConnectorResult(
            provider=self.provider,
            status="FAILED",
            provider_reference=reference,
            provider_code=str(response.status_code),
            error_code="INITIATION_REJECTED",
        )

    async def query_transaction_status(self, provider_reference: str) -> ConnectorResult:
        try:
            response = await self.transport.request(
                "GET",
                f"{self.base_url}/collection/v1_0/requesttopay/{provider_reference}",
                headers=await self._headers(self.market),
                timeout=self.timeout_seconds,
            )
        except TransportError:
            return uncertain(self.provider, provider_reference, "QUERY_UNAVAILABLE")
        status = str(response.data.get("status") or "").upper()
        if status == "SUCCESSFUL":
            return ConnectorResult(
                provider=self.provider,
                status="CONFIRMED",
                provider_reference=provider_reference,
                transaction_id=str(response.data.get("financialTransactionId") or "") or None,
                provider_code=status,
            )
        if status in {"FAILED", "REJECTED", "EXPIRED"}:
            return ConnectorResult(
                provider=self.provider,
                status="FAILED",
                provider_reference=provider_reference,
                provider_code=status,
                error_code="PROVIDER_TERMINAL_FAILURE",
            )
        return uncertain(self.provider, provider_reference, "STATUS_PENDING")
