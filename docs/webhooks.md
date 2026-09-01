# Production webhook and reconciliation integration

Webhook handlers are framework-neutral so FastAPI, Django, or an API gateway can pass the exact request bytes without reserialization.

## Trust model

A callback is an authenticated **occurrence hint**, not proof of financial finality:

1. Reject oversized, malformed, unsupported, or unauthenticated requests.
2. Extract event identity from the authenticated exact body and compute its SHA-256 digest.
3. Pass the decoded object through the mandatory `WebhookPayloadSanitizer`; persist only its provider-specific allowlisted projection and keyed PII fingerprints.
4. Deduplicate on `(provider, event_id)`.
5. In the same database transaction, lock and transition `SUBMITTED → CALLBACK_RECEIVED`.
6. Insert a unique reconciliation outbox job before committing.
7. Lease jobs with `FOR UPDATE SKIP LOCKED`; query the provider head-end.
8. Only the provider query result may transition to `CONFIRMED` or `FAILED`.

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

Use a dedicated database role. The event table contains only a minimized callback projection, but it remains regulated transaction evidence and still requires encryption at rest, access logging, retention limits, and exclusion from application logs.

`WebhookPayloadSanitizer` requires at least 32 bytes of deployment-owned HMAC key material. Inject `AGENTBRIDGE_PII_HMAC_KEY` from a secret manager; never put it in source control, graph state, or the database. HMAC-SHA256 is intentionally used instead of plain salted SHA-256 because phone numbers have a small enumerable keyspace. Include a non-secret `key_id` to support controlled key rotation.

## Framework adapter sketch

```python
from agentbridge.webhooks import WebhookPayloadSanitizer

sanitizer = WebhookPayloadSanitizer.from_environment(key_id="2026-09")
webhook_handler = WebhookHandler(repository, verifiers, sanitizer)

# Preserve request.body exactly; signature checks fail after JSON re-encoding.
body = await request.body()
result = await webhook_handler.handle("paystack", body, request.headers)
# The reconciliation job was committed atomically by ingest_webhook.
return Response(status_code=result.http_status)
```

Always acknowledge authenticated duplicates and unmatched references with generic HTTP 200. Do not expose whether a reference belongs to a customer; unmatched sanitized evidence is retained for delayed matching.

## PostgreSQL concurrency harness

The integration suite applies migrations 001/002 in a disposable PostgreSQL schema and tests:

- ten simultaneous copies of one callback produce one event and one outbox job;
- provider polling and delayed callback ingestion serialize without overwriting `CONFIRMED`;
- closing a connection between state mutation and outbox insertion rolls back all writes;
- simultaneous workers lease distinct jobs with `FOR UPDATE SKIP LOCKED`.

Run the same suite locally with Docker and psycopg installed:

```bash
pip install '.[postgres]'
make test-postgres
```

The local script binds PostgreSQL only to `127.0.0.1`, waits for readiness, runs the integration module, and always removes the container. A ready-to-apply GitHub Actions service fragment is in `docs/ci/postgres-service.yml`; activating it requires GitHub workflow-write permission.

## Polling and settlement

Run a sweeper for `SUBMITTED` records older than the callback SLA and for `CALLBACK_RECEIVED` records not yet reconciled. Bound retries and route exhausted cases into OSCAL findings/manual review. Nightly settlement ingestion should independently compare provider statement totals and references against confirmed internal records.
