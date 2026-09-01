"""Durable payment FSM and repository contracts.

Webhook delivery is an occurrence hint. Only a provider head-end query may
transition a transaction to a final financial state.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Mapping, Protocol
from uuid import UUID


class PaymentStatus(StrEnum):
    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    SUBMITTED = "SUBMITTED"
    CALLBACK_RECEIVED = "CALLBACK_RECEIVED"
    CONFIRMED = "CONFIRMED"
    FAILED = "FAILED"


ALLOWED_TRANSITIONS: dict[PaymentStatus, frozenset[PaymentStatus]] = {
    PaymentStatus.DRAFT: frozenset({PaymentStatus.PENDING_APPROVAL, PaymentStatus.FAILED}),
    PaymentStatus.PENDING_APPROVAL: frozenset({PaymentStatus.SUBMITTED, PaymentStatus.FAILED}),
    PaymentStatus.SUBMITTED: frozenset({PaymentStatus.CALLBACK_RECEIVED, PaymentStatus.CONFIRMED, PaymentStatus.FAILED}),
    PaymentStatus.CALLBACK_RECEIVED: frozenset({PaymentStatus.CONFIRMED, PaymentStatus.FAILED}),
    PaymentStatus.CONFIRMED: frozenset(),
    PaymentStatus.FAILED: frozenset(),
}


@dataclass(frozen=True)
class PaymentTransaction:
    transaction_id: UUID
    run_id: str
    idempotency_key: str
    provider: str
    amount: Decimal
    currency: str
    status: PaymentStatus
    provider_reference: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class WebhookEvent:
    provider: str
    event_id: str
    provider_reference: str
    payload_sha256: str
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class WebhookIngestResult:
    inserted: bool
    matched: bool
    transaction: PaymentTransaction | None
    reconciliation_required: bool


@dataclass(frozen=True)
class ReconciliationJob:
    job_id: UUID
    transaction_id: UUID
    provider: str
    provider_reference: str
    status: PaymentStatus
    attempts: int
    max_attempts: int


class PaymentRepository(Protocol):
    async def get_by_provider_reference(self, provider: str, reference: str) -> PaymentTransaction | None: ...

    async def ingest_webhook(self, event: WebhookEvent) -> WebhookIngestResult:
        """Atomically deduplicate, transition, and enqueue reconciliation."""
        ...

    async def transition(
        self,
        idempotency_key: str,
        from_status: PaymentStatus,
        to_status: PaymentStatus,
        *,
        provider_reference: str | None = None,
    ) -> bool: ...


def assert_transition(from_status: PaymentStatus, to_status: PaymentStatus) -> None:
    if to_status not in ALLOWED_TRANSITIONS[from_status]:
        raise ValueError(f"invalid payment transition: {from_status} -> {to_status}")


class PostgresPaymentRepository:
    """ACID repository using a psycopg AsyncConnectionPool-compatible pool."""

    def __init__(self, pool: Any) -> None:
        self.pool = pool

    async def get_by_provider_reference(self, provider: str, reference: str) -> PaymentTransaction | None:
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                """
                SELECT transaction_id, run_id, idempotency_key, provider, amount,
                       currency, status, provider_reference, created_at, updated_at
                  FROM payment_transactions
                 WHERE provider = %s AND provider_reference = %s
                """,
                (provider, reference),
            )
            row = await cursor.fetchone()
        return None if row is None else _transaction_from_row(row)

    async def ingest_webhook(self, event: WebhookEvent) -> WebhookIngestResult:
        """Commit callback evidence, FSM state, and outbox work as one unit."""
        async with self.pool.connection() as conn:
            async with conn.transaction():
                cursor = await conn.execute(
                    """
                    INSERT INTO payment_webhook_events
                           (provider, event_id, provider_reference, payload_sha256, payload)
                    VALUES (%s, %s, %s, %s, %s::jsonb)
                    ON CONFLICT (provider, event_id) DO NOTHING
                    RETURNING event_pk, provider_reference, payload_sha256
                    """,
                    (
                        event.provider,
                        event.event_id,
                        event.provider_reference,
                        event.payload_sha256,
                        _json(event.payload),
                    ),
                )
                inserted_row = await cursor.fetchone()
                inserted = inserted_row is not None
                if inserted_row is None:
                    cursor = await conn.execute(
                        """
                        SELECT event_pk, provider_reference, payload_sha256
                          FROM payment_webhook_events
                         WHERE provider = %s AND event_id = %s
                        """,
                        (event.provider, event.event_id),
                    )
                    inserted_row = await cursor.fetchone()
                    if inserted_row is None:
                        raise RuntimeError("webhook deduplication row disappeared")
                event_pk, stored_reference, stored_hash = inserted_row
                if (
                    stored_reference != event.provider_reference
                    or stored_hash != event.payload_sha256
                ):
                    raise RuntimeError("webhook event identity collision")

                cursor = await conn.execute(
                    """
                    SELECT transaction_id, run_id, idempotency_key, provider, amount,
                           currency, status, provider_reference, created_at, updated_at
                      FROM payment_transactions
                     WHERE provider = %s AND provider_reference = %s
                     FOR UPDATE
                    """,
                    (event.provider, event.provider_reference),
                )
                row = await cursor.fetchone()
                if row is None:
                    return WebhookIngestResult(inserted, False, None, False)

                transaction = _transaction_from_row(row)
                if transaction.status == PaymentStatus.SUBMITTED:
                    await conn.execute(
                        """
                        UPDATE payment_transactions
                           SET status = 'CALLBACK_RECEIVED', updated_at = NOW()
                         WHERE transaction_id = %s
                        """,
                        (transaction.transaction_id,),
                    )
                    transaction = PaymentTransaction(
                        transaction_id=transaction.transaction_id,
                        run_id=transaction.run_id,
                        idempotency_key=transaction.idempotency_key,
                        provider=transaction.provider,
                        amount=transaction.amount,
                        currency=transaction.currency,
                        status=PaymentStatus.CALLBACK_RECEIVED,
                        provider_reference=transaction.provider_reference,
                        created_at=transaction.created_at,
                        updated_at=transaction.updated_at,
                    )

                reconciliation_required = transaction.status == PaymentStatus.CALLBACK_RECEIVED
                if reconciliation_required:
                    await conn.execute(
                        """
                        INSERT INTO payment_reconciliation_outbox
                               (transaction_id, webhook_event_pk)
                        VALUES (%s, %s)
                        ON CONFLICT (webhook_event_pk) DO NOTHING
                        """,
                        (transaction.transaction_id, event_pk),
                    )
                return WebhookIngestResult(
                    inserted, True, transaction, reconciliation_required
                )

    async def transition(
        self,
        idempotency_key: str,
        from_status: PaymentStatus,
        to_status: PaymentStatus,
        *,
        provider_reference: str | None = None,
    ) -> bool:
        assert_transition(from_status, to_status)
        async with self.pool.connection() as conn:
            async with conn.transaction():
                cursor = await conn.execute(
                    "SELECT status FROM payment_transactions WHERE idempotency_key = %s FOR UPDATE",
                    (idempotency_key,),
                )
                row = await cursor.fetchone()
                if row is None or PaymentStatus(row[0]) != from_status:
                    return False
                await conn.execute(
                    """
                    UPDATE payment_transactions
                       SET status = %s,
                           provider_reference = COALESCE(%s, provider_reference),
                           updated_at = NOW()
                     WHERE idempotency_key = %s
                    """,
                    (to_status.value, provider_reference, idempotency_key),
                )
        return True

    async def claim_reconciliation_jobs(
        self,
        worker_id: str,
        *,
        limit: int = 25,
        lease_seconds: int = 60,
    ) -> list[ReconciliationJob]:
        """Lease due jobs with SKIP LOCKED so workers never double-poll."""
        if not worker_id or limit < 1 or lease_seconds < 1:
            raise ValueError("valid worker_id, limit, and lease_seconds are required")
        async with self.pool.connection() as conn:
            async with conn.transaction():
                # Exhausted pending jobs and expired exhausted leases must not
                # remain permanently claimable or stranded in PROCESSING.
                await conn.execute(
                    """
                    UPDATE payment_reconciliation_outbox
                       SET status = 'DEAD', locked_at = NULL, locked_by = NULL
                     WHERE attempts >= max_attempts
                       AND (
                           status = 'PENDING' OR
                           (status = 'PROCESSING' AND locked_at < NOW() - (%s * INTERVAL '1 second'))
                       )
                    """,
                    (lease_seconds,),
                )
                cursor = await conn.execute(
                    """
                    WITH due AS (
                        SELECT job_id
                          FROM payment_reconciliation_outbox
                         WHERE attempts < max_attempts
                           AND (
                               (status = 'PENDING' AND available_at <= NOW()) OR
                               (status = 'PROCESSING' AND locked_at < NOW() - (%s * INTERVAL '1 second'))
                           )
                         ORDER BY available_at, created_at
                         FOR UPDATE SKIP LOCKED
                         LIMIT %s
                    )
                    UPDATE payment_reconciliation_outbox AS jobs
                       SET status = 'PROCESSING',
                           attempts = jobs.attempts + 1,
                           locked_at = NOW(),
                           locked_by = %s
                      FROM due, payment_transactions AS tx
                     WHERE jobs.job_id = due.job_id
                       AND tx.transaction_id = jobs.transaction_id
                    RETURNING jobs.job_id, jobs.transaction_id, tx.provider,
                              tx.provider_reference, tx.status,
                              jobs.attempts, jobs.max_attempts
                    """,
                    (lease_seconds, limit, worker_id),
                )
                rows = await cursor.fetchall()
        return [
            ReconciliationJob(
                job_id=row[0], transaction_id=row[1], provider=row[2],
                provider_reference=row[3], status=PaymentStatus(row[4]),
                attempts=row[5], max_attempts=row[6],
            )
            for row in rows
        ]

    async def complete_reconciliation_job(self, job_id: UUID, worker_id: str) -> bool:
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                """
                UPDATE payment_reconciliation_outbox
                   SET status = 'COMPLETED', completed_at = NOW(),
                       locked_at = NULL, locked_by = NULL
                 WHERE job_id = %s AND status = 'PROCESSING' AND locked_by = %s
                RETURNING job_id
                """,
                (job_id, worker_id),
            )
            row = await cursor.fetchone()
            await conn.commit()
        return row is not None

    async def reschedule_reconciliation_job(
        self,
        job_id: UUID,
        worker_id: str,
        *,
        delay_seconds: int,
        error_code: str,
    ) -> bool:
        if delay_seconds < 1 or not error_code or len(error_code) > 64:
            raise ValueError("valid delay_seconds and bounded error_code are required")
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                """
                UPDATE payment_reconciliation_outbox
                   SET status = CASE WHEN attempts >= max_attempts THEN 'DEAD' ELSE 'PENDING' END,
                       available_at = NOW() + (%s * INTERVAL '1 second'),
                       last_error_code = %s,
                       locked_at = NULL,
                       locked_by = NULL
                 WHERE job_id = %s AND status = 'PROCESSING' AND locked_by = %s
                RETURNING job_id
                """,
                (delay_seconds, error_code, job_id, worker_id),
            )
            row = await cursor.fetchone()
            await conn.commit()
        return row is not None


def _transaction_from_row(row: Any) -> PaymentTransaction:
    return PaymentTransaction(
        transaction_id=row[0], run_id=row[1], idempotency_key=row[2], provider=row[3],
        amount=row[4], currency=row[5], status=PaymentStatus(row[6]),
        provider_reference=row[7], created_at=row[8], updated_at=row[9],
    )


def _json(value: Mapping[str, Any]) -> str:
    import json

    return json.dumps(value, separators=(",", ":"), sort_keys=True)
