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


class PaymentRepository(Protocol):
    async def get_by_provider_reference(self, provider: str, reference: str) -> PaymentTransaction | None: ...

    async def record_webhook(self, event: WebhookEvent) -> bool:
        """Return False when the provider/event ID has already been recorded."""
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
        if row is None:
            return None
        return PaymentTransaction(
            transaction_id=row[0], run_id=row[1], idempotency_key=row[2], provider=row[3],
            amount=row[4], currency=row[5], status=PaymentStatus(row[6]),
            provider_reference=row[7], created_at=row[8], updated_at=row[9],
        )

    async def record_webhook(self, event: WebhookEvent) -> bool:
        async with self.pool.connection() as conn:
            cursor = await conn.execute(
                """
                INSERT INTO payment_webhook_events
                       (provider, event_id, provider_reference, payload_sha256, payload)
                VALUES (%s, %s, %s, %s, %s::jsonb)
                ON CONFLICT (provider, event_id) DO NOTHING
                RETURNING event_id
                """,
                (
                    event.provider,
                    event.event_id,
                    event.provider_reference,
                    event.payload_sha256,
                    _json(event.payload),
                ),
            )
            inserted = await cursor.fetchone()
            await conn.commit()
        return inserted is not None

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


def _json(value: Mapping[str, Any]) -> str:
    import json

    return json.dumps(value, separators=(",", ":"), sort_keys=True)
