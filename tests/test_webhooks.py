import asyncio
import hashlib
import hmac
import json
from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

from agentbridge.core.payment_lifecycle import (
    PaymentStatus,
    PaymentTransaction,
    WebhookEvent,
    assert_transition,
)
from agentbridge.webhooks.handlers import PaymentReconciler, WebhookHandler
from agentbridge.webhooks.security import PaystackSignatureVerifier, WebhookAuthenticationError


class MemoryRepository:
    def __init__(self, transaction):
        self.transaction = transaction
        self.events = set()

    async def get_by_provider_reference(self, provider, reference):
        if self.transaction.provider == provider and self.transaction.provider_reference == reference:
            return self.transaction
        return None

    async def record_webhook(self, event: WebhookEvent):
        key = (event.provider, event.event_id)
        if key in self.events:
            return False
        self.events.add(key)
        return True

    async def transition(self, idempotency_key, from_status, to_status, *, provider_reference=None):
        if self.transaction.idempotency_key != idempotency_key or self.transaction.status != from_status:
            return False
        assert_transition(from_status, to_status)
        self.transaction = replace(
            self.transaction,
            status=to_status,
            provider_reference=provider_reference or self.transaction.provider_reference,
        )
        return True


def _transaction(status=PaymentStatus.SUBMITTED):
    return PaymentTransaction(
        transaction_id=uuid4(),
        run_id="run-1",
        idempotency_key="idem-12345",
        provider="paystack",
        provider_reference="pay-ref-1",
        amount=Decimal("100.00"),
        currency="NGN",
        status=status,
    )


def test_authenticated_webhook_is_hint_then_head_end_confirms():
    async def scenario():
        secret = b"paystack-secret"
        body = json.dumps({"event": "charge.success", "data": {"reference": "pay-ref-1"}}).encode()
        signature = hmac.new(secret, body, hashlib.sha512).hexdigest()
        repository = MemoryRepository(_transaction())
        handler = WebhookHandler(repository, {"paystack": PaystackSignatureVerifier(secret)})

        result = await handler.handle("paystack", body, {"x-paystack-signature": signature})
        assert result.reconciliation_required is True
        assert repository.transaction.status == PaymentStatus.CALLBACK_RECEIVED

        class Client:
            async def query_status(self, provider_reference):
                assert provider_reference == "pay-ref-1"
                return "success"

        final = await PaymentReconciler(repository, {"paystack": Client()}).reconcile(repository.transaction)
        assert final == PaymentStatus.CONFIRMED
        assert repository.transaction.status == PaymentStatus.CONFIRMED

        duplicate = await handler.handle("paystack", body, {"x-paystack-signature": signature})
        assert duplicate.duplicate is True

    asyncio.run(scenario())


def test_bad_webhook_signature_fails_before_database_write():
    async def scenario():
        repository = MemoryRepository(_transaction())
        handler = WebhookHandler(repository, {"paystack": PaystackSignatureVerifier(b"secret")})
        try:
            await handler.handle(
                "paystack",
                b'{"data":{"reference":"pay-ref-1"}}',
                {"x-paystack-signature": "invalid"},
            )
            raise AssertionError("invalid signature must fail")
        except WebhookAuthenticationError:
            pass
        assert repository.events == set()
        assert repository.transaction.status == PaymentStatus.SUBMITTED

    asyncio.run(scenario())


def test_terminal_payment_state_cannot_transition_again():
    try:
        assert_transition(PaymentStatus.CONFIRMED, PaymentStatus.FAILED)
        raise AssertionError("terminal state must be immutable")
    except ValueError:
        pass
