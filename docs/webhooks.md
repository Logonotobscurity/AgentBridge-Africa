# Production webhook and reconciliation integration

Webhook handlers are framework-neutral so FastAPI, Django, or an API gateway can pass the exact request bytes without reserialization.

## Trust model

A callback is an authenticated **occurrence hint**, not proof of financial finality:

1. Reject oversized, malformed, unsupported, or unauthenticated requests.
2. Deduplicate on `(provider, event_id)`.
3. Store the event and SHA-256 body digest.
4. In the same database transaction, lock and transition `SUBMITTED → CALLBACK_RECEIVED`.
5. Insert a unique reconciliation outbox job before committing.
6. Lease jobs with `FOR UPDATE SKIP LOCKED`; query the provider head-end.
7. Only the query result may transition to `CONFIRMED` or `FAILED`.

Paystack callbacks use HMAC-SHA512 over the exact body. MTN deployments can use a provider callback token. M-Pesa endpoints should sit behind an mTLS workload proxy; `SpiffeProxyVerifier` trusts identity headers only when the proxy also supplies its verification marker. The proxy must strip client-supplied identity headers.

## Database

Apply `agentbridge/migrations/001_payment_lifecycle.sql` and then `002_atomic_callback_outbox.sql` before traffic. They provide:

- a unique idempotency key;
- a unique provider/reference pair;
- constrained FSM statuses;
- deduplicated webhook events;
- indexes for pending reconciliation;
- database-enforced transition parity;
- an atomic callback/FSM/outbox bundle;
- leased, bounded reconciliation jobs using `SELECT ... FOR UPDATE SKIP LOCKED`.

Use a dedicated database role. Webhook payload JSON may contain regulated data and should be encrypted at rest, access logged, retention-limited, and excluded from application logs.

## Framework adapter sketch

```python
# Preserve request.body exactly; signature checks fail after JSON re-encoding.
body = await request.body()
result = await webhook_handler.handle("paystack", body, request.headers)
# The reconciliation job was committed atomically by ingest_webhook.
return Response(status_code=result.http_status)
```

Always acknowledge authenticated duplicates with HTTP 200. Do not expose whether an unmatched reference belongs to a customer; the generic handler returns HTTP 202 and retains it for delayed matching.

## PostgreSQL concurrency harness

CI starts PostgreSQL 16 and sets `AGENTBRIDGE_TEST_POSTGRES_DSN`. The integration suite applies migrations 001/002 in a disposable schema and tests:

- ten simultaneous copies of one callback produce one event and one outbox job;
- provider polling and delayed callback ingestion serialize without overwriting `CONFIRMED`;
- closing a connection between state mutation and outbox insertion rolls back all writes;
- simultaneous workers lease distinct jobs with `FOR UPDATE SKIP LOCKED`.

Run the same suite locally with Docker and psycopg installed:

```bash
pip install '.[postgres]'
make test-postgres
```

The local script binds PostgreSQL only to `127.0.0.1`, waits for readiness, runs the integration module, and always removes the container.

## Polling and settlement

Run a sweeper for `SUBMITTED` records older than the callback SLA and for `CALLBACK_RECEIVED` records not yet reconciled. Bound retries and route exhausted cases into OSCAL findings/manual review. Nightly settlement ingestion should independently compare provider statement totals and references against confirmed internal records.
