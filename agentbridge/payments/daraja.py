"""Safaricom Daraja STK capability pack."""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Mapping
from zoneinfo import ZoneInfo

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
    validate_callback_url,
    validate_limit,
)


def normalize_ke_msisdn(value: str) -> str:
    digits = "".join(character for character in value if character.isdigit())
    if digits.startswith("0"):
        digits = "254" + digits[1:]
    if not (len(digits) == 12 and digits.startswith("254") and digits[3] in {"1", "7"}):
        raise ValueError("recipient must be a Kenyan mobile number")
    return digits


@dataclass
class DarajaConnector:
    callback_url: str
    callback_hosts: frozenset[str]
    recipient_resolver: RecipientResolver
    environment: Environment = Environment.PRODUCTION
    transport: AsyncTransport = field(default_factory=HttpxTransport)
    secrets: SecretResolver = field(default_factory=RuntimeSecretsManager)
    limits: Mapping[tuple[str, Currency], PaymentLimit] = field(default_factory=lambda: dict(DEFAULT_LIMITS))
    timeout_seconds: float = 10.0
    provider: str = field(default="daraja", init=False)

    def __post_init__(self) -> None:
        self.callback_url = validate_callback_url(self.callback_url, self.callback_hosts)

    @property
    def base_url(self) -> str:
        return ALLOWLIST_ENDPOINTS[self.provider][self.environment]

    async def _credentials_and_token(self):
        credentials = self.secrets.resolve(
            self.provider, ("consumer_key", "consumer_secret", "passkey", "shortcode")
        )
        response = await self.transport.request(
            "GET",
            f"{self.base_url}/oauth/v1/generate?grant_type=client_credentials",
            headers={
                "Authorization": basic_auth(
                    credentials.reveal("consumer_key"), credentials.reveal("consumer_secret")
                )
            },
            timeout=self.timeout_seconds,
        )
        token = str(response.data.get("access_token") or "")
        if response.status_code != 200 or not token:
            raise TransportError("Daraja access token unavailable")
        return credentials, token

    async def initiate_payment(self, intent: PaymentIntent) -> ConnectorResult:
        if intent.destination_country is not Country.KE or intent.currency is not Currency.KES:
            raise ValueError("Daraja STK requires a KE/KES payment intent")
        validate_limit(self.provider, intent, self.limits)
        whole_amount = intent.amount.quantize(Decimal("1"))
        if whole_amount != intent.amount:
            raise ValueError("Daraja STK amount must be a whole KES value")
        phone = normalize_ke_msisdn(self.recipient_resolver.resolve(intent.recipient_ref))
        reference = str(intent.intent_id)
        # Authentication happens before the ambiguous money-moving request.
        # Auth failure is retry-safe and must not be recorded as a submitted payment.
        credentials, token = await self._credentials_and_token()
        timestamp = datetime.now(ZoneInfo("Africa/Nairobi")).strftime("%Y%m%d%H%M%S")
        shortcode = credentials.reveal("shortcode")
        password = base64.b64encode(
            f"{shortcode}{credentials.reveal('passkey')}{timestamp}".encode()
        ).decode()
        try:
            response = await self.transport.request(
                "POST",
                f"{self.base_url}/mpesa/stkpush/v1/processrequest",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={
                    "BusinessShortCode": shortcode,
                    "Password": password,
                    "Timestamp": timestamp,
                    "TransactionType": "CustomerPayBillOnline",
                    "Amount": str(whole_amount),
                    "PartyA": phone,
                    "PartyB": shortcode,
                    "PhoneNumber": phone,
                    "CallBackURL": self.callback_url,
                    "AccountReference": intent.merchant_reference,
                    "TransactionDesc": "AgentBridge payment",
                },
                timeout=self.timeout_seconds,
            )
        except TransportError:
            return uncertain(self.provider, reference, "TRANSPORT_UNCERTAIN")

        checkout_id = str(response.data.get("CheckoutRequestID") or reference)
        if ambiguous_http_status(response.status_code):
            return uncertain(self.provider, checkout_id, "PROVIDER_UNAVAILABLE")
        if response.status_code in {200, 201} and str(response.data.get("ResponseCode")) == "0":
            return ConnectorResult(
                provider=self.provider,
                status="SUBMITTED",
                provider_reference=checkout_id,
                provider_code="0",
                requires_reconciliation=True,
            )
        return ConnectorResult(
            provider=self.provider,
            status="FAILED",
            provider_reference=checkout_id,
            provider_code=str(response.data.get("ResponseCode") or response.status_code),
            error_code="INITIATION_REJECTED",
        )

    async def query_transaction_status(self, provider_reference: str) -> ConnectorResult:
        try:
            credentials, token = await self._credentials_and_token()
            timestamp = datetime.now(ZoneInfo("Africa/Nairobi")).strftime("%Y%m%d%H%M%S")
            shortcode = credentials.reveal("shortcode")
            password = base64.b64encode(
                f"{shortcode}{credentials.reveal('passkey')}{timestamp}".encode()
            ).decode()
            response = await self.transport.request(
                "POST",
                f"{self.base_url}/mpesa/stkpushquery/v1/query",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={
                    "BusinessShortCode": shortcode,
                    "Password": password,
                    "Timestamp": timestamp,
                    "CheckoutRequestID": provider_reference,
                },
                timeout=self.timeout_seconds,
            )
        except TransportError:
            return uncertain(self.provider, provider_reference, "QUERY_UNAVAILABLE")
        result_code = response.data.get("ResultCode")
        if str(result_code) == "0":
            return ConnectorResult(
                provider=self.provider,
                status="CONFIRMED",
                provider_reference=provider_reference,
                provider_code="0",
            )
        if result_code is None or response.status_code >= 500:
            return uncertain(self.provider, provider_reference, "STATUS_PENDING")
        return ConnectorResult(
            provider=self.provider,
            status="FAILED",
            provider_reference=provider_reference,
            provider_code=str(result_code),
            error_code="PROVIDER_TERMINAL_FAILURE",
        )
