"""Framework-neutral webhook receiver and hybrid reconciliation service."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from agentbridge.core.payment_lifecycle import (
    PaymentRepository,
    PaymentStatus,
    PaymentTransaction,
    WebhookEvent,
)
from agentbridge.webhooks.security import WebhookVerifier

MAX_WEBHOOK_BYTES = 1_048_576


class InvalidWebhook(ValueError):
    pass


@dataclass(frozen=True)
class WebhookResult:
    http_status: int
    accepted: bool
    duplicate: bool = False
    matched: bool = False
    reconciliation_required: bool = False


class ProviderStatusClient(Protocol):
    async def query_status(self, provider_reference: str) -> str: ...


class WebhookHandler:
    """Authenticate, deduplicate, and record a callback as an unverified hint."""

    def __init__(self, repository: PaymentRepository, verifiers: Mapping[str, WebhookVerifier]) -> None:
        self.repository = repository
        self.verifiers = dict(verifiers)

    async def handle(self, provider: str, body: bytes, headers: Mapping[str, str]) -> WebhookResult:
        provider = provider.lower()
        if len(body) > MAX_WEBHOOK_BYTES:
            raise InvalidWebhook("webhook payload exceeds 1 MiB")
        verifier = self.verifiers.get(provider)
        if verifier is None:
            raise InvalidWebhook(f"unsupported webhook provider: {provider}")
        verifier.verify(body, headers)

        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise InvalidWebhook("webhook body must be a JSON object") from exc
        if not isinstance(payload, dict):
            raise InvalidWebhook("webhook body must be a JSON object")

        reference, event_id = _event_identity(provider, payload, headers, body)
        event = WebhookEvent(
            provider=provider,
            event_id=event_id,
            provider_reference=reference,
            payload_sha256=hashlib.sha256(body).hexdigest(),
            payload=payload,
        )
        inserted = await self.repository.record_webhook(event)
        if not inserted:
            return WebhookResult(http_status=200, accepted=True, duplicate=True)

        transaction = await self.repository.get_by_provider_reference(provider, reference)
        if transaction is None:
            # Keep the authenticated event for delayed matching, but disclose no
            # transaction information to the sender.
            return WebhookResult(http_status=202, accepted=True, matched=False)

        transitioned = False
        if transaction.status is PaymentStatus.SUBMITTED:
            transitioned = await self.repository.transition(
                transaction.idempotency_key,
                PaymentStatus.SUBMITTED,
                PaymentStatus.CALLBACK_RECEIVED,
            )
        return WebhookResult(
            http_status=200,
            accepted=True,
            matched=True,
            reconciliation_required=transitioned or transaction.status is PaymentStatus.CALLBACK_RECEIVED,
        )


class PaymentReconciler:
    """Poll the provider head-end before assigning final financial status."""

    SUCCESS = frozenset({"success", "successful", "confirmed", "completed", "settled"})
    FAILURE = frozenset({"failed", "cancelled", "canceled", "rejected", "expired"})

    def __init__(self, repository: PaymentRepository, clients: Mapping[str, ProviderStatusClient]) -> None:
        self.repository = repository
        self.clients = dict(clients)

    async def reconcile(self, transaction: PaymentTransaction) -> PaymentStatus:
        if transaction.status not in {PaymentStatus.SUBMITTED, PaymentStatus.CALLBACK_RECEIVED}:
            return transaction.status
        if not transaction.provider_reference:
            raise ValueError("provider_reference is required for reconciliation")
        client = self.clients[transaction.provider]
        provider_status = (await client.query_status(transaction.provider_reference)).lower()
        if provider_status in self.SUCCESS:
            target = PaymentStatus.CONFIRMED
        elif provider_status in self.FAILURE:
            target = PaymentStatus.FAILED
        else:
            return transaction.status
        changed = await self.repository.transition(
            transaction.idempotency_key,
            transaction.status,
            target,
        )
        return target if changed else transaction.status


def _event_identity(
    provider: str,
    payload: Mapping[str, Any],
    headers: Mapping[str, str],
    body: bytes,
) -> tuple[str, str]:
    normalized = {key.lower(): value for key, value in headers.items()}
    reference = ""
    event_id = ""
    if provider == "paystack":
        data = payload.get("data") if isinstance(payload.get("data"), Mapping) else {}
        reference = str(data.get("reference") or "")
        event_id = str(normalized.get("x-paystack-event-id") or "")
    elif provider == "mpesa":
        root = payload.get("Body") if isinstance(payload.get("Body"), Mapping) else {}
        callback = root.get("stkCallback") if isinstance(root.get("stkCallback"), Mapping) else {}
        reference = str(callback.get("CheckoutRequestID") or "")
        event_id = str(callback.get("MerchantRequestID") or reference)
    elif provider == "mtn_momo":
        reference = str(
            payload.get("financialTransactionId")
            or payload.get("externalId")
            or normalized.get("x-reference-id")
            or ""
        )
        event_id = str(normalized.get("x-callback-event-id") or reference)
    if not reference:
        raise InvalidWebhook("provider reference is missing")
    if not event_id:
        event_id = hashlib.sha256(provider.encode() + b":" + body).hexdigest()
    return reference[:128], event_id[:160]
