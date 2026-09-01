"""Real PostgreSQL concurrency and rollback tests.

Set AGENTBRIDGE_TEST_POSTGRES_DSN to run. CI supplies PostgreSQL 16.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest

TEST_DSN = os.getenv("AGENTBRIDGE_TEST_POSTGRES_DSN")
if not TEST_DSN:
    pytest.skip("real PostgreSQL DSN not configured", allow_module_level=True)

psycopg = pytest.importorskip("psycopg")
psycopg_pool = pytest.importorskip("psycopg_pool")
from psycopg.conninfo import make_conninfo  # noqa: E402

from agentbridge.core.payment_lifecycle import (  # noqa: E402
    PaymentStatus,
    PostgresPaymentRepository,
    WebhookEvent,
)
from agentbridge.migrations import MIGRATION_DIR  # noqa: E402

pytestmark = pytest.mark.integration


def _schema_dsn(dsn: str, schema: str) -> str:
    return make_conninfo(dsn, options=f"-c search_path={schema},public")


@pytest.fixture(scope="module")
def postgres_dsn():
    schema = f"agentbridge_test_{uuid4().hex}"
    with psycopg.connect(TEST_DSN, autocommit=True) as conn:
        conn.execute(f'CREATE SCHEMA "{schema}"')
        conn.execute(f'SET search_path TO "{schema}", public')
        for migration in ("001_payment_lifecycle.sql", "002_atomic_callback_outbox.sql"):
            conn.execute((MIGRATION_DIR / migration).read_text())
    yield _schema_dsn(TEST_DSN, schema)
    with psycopg.connect(TEST_DSN, autocommit=True) as conn:
        conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')


@pytest.fixture(autouse=True)
def clean_ledger(postgres_dsn):
    with psycopg.connect(postgres_dsn, autocommit=True) as conn:
        conn.execute(
            "TRUNCATE payment_reconciliation_outbox, payment_webhook_events, payment_transactions CASCADE"
        )


def _insert_transaction(dsn: str, reference: str, *, idempotency_key: str | None = None) -> None:
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            """
            INSERT INTO payment_transactions
                   (run_id, idempotency_key, provider, provider_reference,
                    amount, currency, status)
            VALUES (%s, %s, 'paystack', %s, 1500.00, 'NGN', 'SUBMITTED')
            """,
            (f"run-{reference}", idempotency_key or f"idem-{reference}", reference),
        )


def _event(reference: str, event_id: str) -> WebhookEvent:
    payload = {"event": "charge.success", "data": {"reference": reference}}
    encoded = json.dumps(payload, sort_keys=True).encode()
    return WebhookEvent(
        provider="paystack",
        event_id=event_id,
        provider_reference=reference,
        payload_sha256=hashlib.sha256(encoded).hexdigest(),
        payload=payload,
    )


def test_concurrent_callback_retry_storm_commits_once(postgres_dsn):
    reference = f"ref-{uuid4().hex}"
    event = _event(reference, f"event-{uuid4().hex}")
    _insert_transaction(postgres_dsn, reference)

    async def scenario():
        pool = psycopg_pool.AsyncConnectionPool(postgres_dsn, min_size=1, max_size=12, open=False)
        await pool.open()
        try:
            repository = PostgresPaymentRepository(pool)
            return await asyncio.gather(*(repository.ingest_webhook(event) for _ in range(10)))
        finally:
            await pool.close()

    outcomes = asyncio.run(scenario())
    assert sum(outcome.inserted for outcome in outcomes) == 1
    assert all(outcome.matched and outcome.reconciliation_required for outcome in outcomes)

    with psycopg.connect(postgres_dsn) as conn:
        status = conn.execute(
            "SELECT status FROM payment_transactions WHERE provider_reference = %s", (reference,)
        ).fetchone()[0]
        event_count = conn.execute(
            "SELECT count(*) FROM payment_webhook_events WHERE provider_reference = %s", (reference,)
        ).fetchone()[0]
        job_count = conn.execute(
            """
            SELECT count(*) FROM payment_reconciliation_outbox AS jobs
            JOIN payment_transactions AS tx USING (transaction_id)
            WHERE tx.provider_reference = %s
            """,
            (reference,),
        ).fetchone()[0]
    assert status == "CALLBACK_RECEIVED"
    assert event_count == 1
    assert job_count == 1


def test_poller_and_delayed_webhook_serialize_on_payment_row(postgres_dsn):
    reference = f"race-{uuid4().hex}"
    _insert_transaction(postgres_dsn, reference)
    event = _event(reference, f"event-{uuid4().hex}")

    async def scenario():
        pool = psycopg_pool.AsyncConnectionPool(postgres_dsn, min_size=2, max_size=4, open=False)
        await pool.open()
        locked = asyncio.Event()
        release = asyncio.Event()

        async def poller_confirms():
            async with pool.connection() as conn:
                async with conn.transaction():
                    await conn.execute(
                        "SELECT status FROM payment_transactions WHERE provider_reference = %s FOR UPDATE",
                        (reference,),
                    )
                    locked.set()
                    await release.wait()
                    await conn.execute(
                        "UPDATE payment_transactions SET status = 'CONFIRMED' WHERE provider_reference = %s",
                        (reference,),
                    )

        async def delayed_webhook():
            await locked.wait()
            return await PostgresPaymentRepository(pool).ingest_webhook(event)

        poller = asyncio.create_task(poller_confirms())
        webhook = asyncio.create_task(delayed_webhook())
        await locked.wait()
        await asyncio.sleep(0.05)
        release.set()
        await poller
        outcome = await webhook
        await pool.close()
        return outcome

    outcome = asyncio.run(scenario())
    assert outcome.matched is True
    assert outcome.transaction.status == PaymentStatus.CONFIRMED
    assert outcome.reconciliation_required is False
    with psycopg.connect(postgres_dsn) as conn:
        assert conn.execute(
            "SELECT status FROM payment_transactions WHERE provider_reference = %s", (reference,)
        ).fetchone()[0] == "CONFIRMED"
        assert conn.execute("SELECT count(*) FROM payment_reconciliation_outbox").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM payment_webhook_events").fetchone()[0] == 1


def test_connection_loss_rolls_back_event_state_and_outbox(postgres_dsn):
    reference = f"rollback-{uuid4().hex}"
    event = _event(reference, f"event-{uuid4().hex}")
    _insert_transaction(postgres_dsn, reference)

    # Deliberately close a connection after evidence and state writes but before
    # the outbox insert/commit. PostgreSQL must roll the entire transaction back.
    conn = psycopg.connect(postgres_dsn)
    conn.execute(
        """
        INSERT INTO payment_webhook_events
               (provider, event_id, provider_reference, payload_sha256, payload)
        VALUES (%s, %s, %s, %s, %s::jsonb)
        """,
        (event.provider, event.event_id, reference, event.payload_sha256, json.dumps(event.payload)),
    )
    conn.execute(
        "SELECT status FROM payment_transactions WHERE provider_reference = %s FOR UPDATE",
        (reference,),
    )
    conn.execute(
        "UPDATE payment_transactions SET status = 'CALLBACK_RECEIVED' WHERE provider_reference = %s",
        (reference,),
    )
    conn.close()

    with psycopg.connect(postgres_dsn) as verify:
        assert verify.execute("SELECT count(*) FROM payment_webhook_events").fetchone()[0] == 0
        assert verify.execute("SELECT count(*) FROM payment_reconciliation_outbox").fetchone()[0] == 0
        assert verify.execute(
            "SELECT status FROM payment_transactions WHERE provider_reference = %s", (reference,)
        ).fetchone()[0] == "SUBMITTED"

    async def retry():
        pool = psycopg_pool.AsyncConnectionPool(postgres_dsn, min_size=1, max_size=2, open=False)
        await pool.open()
        try:
            return await PostgresPaymentRepository(pool).ingest_webhook(event)
        finally:
            await pool.close()

    outcome = asyncio.run(retry())
    assert outcome.inserted and outcome.reconciliation_required
    with psycopg.connect(postgres_dsn) as verify:
        assert verify.execute("SELECT count(*) FROM payment_webhook_events").fetchone()[0] == 1
        assert verify.execute("SELECT count(*) FROM payment_reconciliation_outbox").fetchone()[0] == 1


def test_skip_locked_leases_each_job_to_at_most_one_worker(postgres_dsn):
    refs = [f"lease-{uuid4().hex}" for _ in range(2)]
    for index, reference in enumerate(refs):
        _insert_transaction(postgres_dsn, reference)

    async def scenario():
        pool = psycopg_pool.AsyncConnectionPool(postgres_dsn, min_size=2, max_size=6, open=False)
        await pool.open()
        repository = PostgresPaymentRepository(pool)
        try:
            await asyncio.gather(
                *(repository.ingest_webhook(_event(ref, f"event-{ref}")) for ref in refs)
            )
            first, second = await asyncio.gather(
                repository.claim_reconciliation_jobs("worker-a", limit=1),
                repository.claim_reconciliation_jobs("worker-b", limit=1),
            )
            return first + second
        finally:
            await pool.close()

    jobs = asyncio.run(scenario())
    assert len(jobs) == 2
    assert len({job.job_id for job in jobs}) == 2
    with psycopg.connect(postgres_dsn) as conn:
        locks = conn.execute(
            "SELECT count(DISTINCT locked_by), count(*) FROM payment_reconciliation_outbox WHERE status = 'PROCESSING'"
        ).fetchone()
    assert locks == (2, 2)
