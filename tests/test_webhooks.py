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
    ReconciliationJob,
    WebhookEvent,
    WebhookIngestResult,
    assert_transition,
)
from agentbridge.webhooks.handlers import OutboxReconciliationWorker, PaymentReconciler, WebhookHandler
from agentbridge.webhooks.redaction import WebhookPayloadSanitizer, WebhookRedactionError
from agentbridge.webhooks.security import PaystackSignatureVerifier, WebhookAuthenticationError


PII_HMAC_KEY = b"test-only-pii-hmac-key-material-32-bytes"


def _sanitizer() -> WebhookPayloadSanitizer:
    return WebhookPayloadSanitizer(PII_HMAC_KEY, key_id="test-v1")


class MemoryRepository:
    def __init__(self, transaction):
        self.transaction = transaction
        self.events = {}
        self.last_event = None

    async def get_by_provider_reference(self, provider, reference):
        if self.transaction.provider == provider and self.transaction.provider_reference == reference:
            return self.transaction
        return None

    async def ingest_webhook(self, event: WebhookEvent):
        self.last_event = event
        key = (event.provider, event.event_id)
        stored = self.events.get(key)
        identity = (event.provider_reference, event.payload_sha256)
        if stored is not None and stored != identity:
            raise RuntimeError("webhook event identity collision")
        inserted = key not in self.events
        self.events[key] = identity
        transaction = await self.get_by_provider_reference(event.provider, event.provider_reference)
        if transaction is None:
            return WebhookIngestResult(inserted, False, None, False)
        if transaction.status == PaymentStatus.SUBMITTED:
            await self.transition(
                transaction.idempotency_key,
                PaymentStatus.SUBMITTED,
                PaymentStatus.CALLBACK_RECEIVED,
            )
            transaction = self.transaction
        return WebhookIngestResult(
            inserted,
            True,
            transaction,
            transaction.status == PaymentStatus.CALLBACK_RECEIVED,
        )

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
        handler = WebhookHandler(repository, {"paystack": PaystackSignatureVerifier(secret)}, _sanitizer())

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


def test_reconciler_accepts_normalized_connector_result():
    async def scenario():
        from agentbridge.payments.models import ConnectorResult

        repository = MemoryRepository(_transaction(PaymentStatus.CALLBACK_RECEIVED))

        class Connector:
            async def query_transaction_status(self, provider_reference):
                return ConnectorResult(
                    provider="paystack",
                    status="CONFIRMED",
                    provider_reference=provider_reference,
                )

        result = await PaymentReconciler(repository, {"paystack": Connector()}).reconcile(
            repository.transaction
        )
        assert result == PaymentStatus.CONFIRMED

    asyncio.run(scenario())


def test_outbox_worker_completes_only_after_head_end_confirmation():
    async def scenario():
        repository = MemoryRepository(_transaction(PaymentStatus.CALLBACK_RECEIVED))
        job = ReconciliationJob(
            job_id=uuid4(),
            transaction_id=repository.transaction.transaction_id,
            provider="paystack",
            provider_reference="pay-ref-1",
            status=PaymentStatus.CALLBACK_RECEIVED,
            attempts=1,
            max_attempts=8,
        )
        repository.completed = False

        async def claim(worker_id, *, limit=25):
            return [job]

        async def complete(job_id, worker_id):
            repository.completed = True
            return True

        async def reschedule(*args, **kwargs):
            raise AssertionError("confirmed payment must not be rescheduled")

        repository.claim_reconciliation_jobs = claim
        repository.complete_reconciliation_job = complete
        repository.reschedule_reconciliation_job = reschedule

        class Connector:
            async def query_transaction_status(self, provider_reference):
                from agentbridge.payments.models import ConnectorResult

                return ConnectorResult(
                    provider="paystack",
                    status="CONFIRMED",
                    provider_reference=provider_reference,
                )

        processed = await OutboxReconciliationWorker(
            repository, {"paystack": Connector()}
        ).run_once("worker-1")
        assert processed == 1
        assert repository.completed is True
        assert repository.transaction.status == PaymentStatus.CONFIRMED

    asyncio.run(scenario())


def test_bad_webhook_signature_fails_before_database_write():
    async def scenario():
        repository = MemoryRepository(_transaction())
        handler = WebhookHandler(repository, {"paystack": PaystackSignatureVerifier(b"secret")}, _sanitizer())
        try:
            await handler.handle(
                "paystack",
                b'{"data":{"reference":"pay-ref-1"}}',
                {"x-paystack-signature": "invalid"},
            )
            raise AssertionError("invalid signature must fail")
        except WebhookAuthenticationError:
            pass
        assert repository.events == {}
        assert repository.transaction.status == PaymentStatus.SUBMITTED

    asyncio.run(scenario())


def test_duplicate_orphan_callback_can_be_matched_later():
    async def scenario():
        secret = b"secret"
        body = json.dumps({"event": "charge.success", "data": {"reference": "late-ref"}}).encode()
        signature = hmac.new(secret, body, hashlib.sha512).hexdigest()
        repository = MemoryRepository(_transaction())
        handler = WebhookHandler(repository, {"paystack": PaystackSignatureVerifier(secret)}, _sanitizer())

        first = await handler.handle("paystack", body, {"x-paystack-signature": signature})
        assert first.http_status == 200 and first.matched is False

        repository.transaction = replace(repository.transaction, provider_reference="late-ref")
        retry = await handler.handle("paystack", body, {"x-paystack-signature": signature})
        assert retry.duplicate is True and retry.reconciliation_required is True
        assert repository.transaction.status == PaymentStatus.CALLBACK_RECEIVED

    asyncio.run(scenario())


def test_duplicate_event_id_with_changed_payload_is_rejected():
    async def scenario():
        secret = b"secret"
        repository = MemoryRepository(_transaction())
        handler = WebhookHandler(repository, {"paystack": PaystackSignatureVerifier(secret)}, _sanitizer())
        headers = {"x-paystack-event-id": "event-1"}
        first = json.dumps({"data": {"reference": "pay-ref-1"}}).encode()
        headers["x-paystack-signature"] = hmac.new(secret, first, hashlib.sha512).hexdigest()
        await handler.handle("paystack", first, headers)

        changed = json.dumps({"data": {"reference": "other-ref"}}).encode()
        headers["x-paystack-signature"] = hmac.new(secret, changed, hashlib.sha512).hexdigest()
        try:
            await handler.handle("paystack", changed, headers)
            raise AssertionError("event identity collision must fail")
        except RuntimeError as exc:
            assert str(exc) == "webhook event identity collision"

    asyncio.run(scenario())


def test_atomic_outbox_migration_enforces_fsm_and_leasing():
    from agentbridge.migrations import MIGRATION_DIR

    sql = (MIGRATION_DIR / "002_atomic_callback_outbox.sql").read_text()
    assert "payment_reconciliation_outbox" in sql
    assert "enforce_payment_status_transition" in sql
    assert "uq_confirmed_provider_receipt" in sql


def test_terminal_payment_state_cannot_transition_again():
    try:
        assert_transition(PaymentStatus.CONFIRMED, PaymentStatus.FAILED)
        raise AssertionError("terminal state must be immutable")
    except ValueError:
        pass
