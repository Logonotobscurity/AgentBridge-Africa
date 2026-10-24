# Production Activation Verification Audit

**Project:** AgentBridge Africa
**Audit date:** 2026-09-01
**Audited revision:** `e4cdca5` (`arena/01a05d72-agentbridge-africa`)
**Scope:** repository evidence for payment FSM, callback ingestion, reconciliation, CI, connectors, secrets, deployment, telemetry, and OSCAL
**Decision:** **NO-GO for live money movement**

## 1. Executive conclusion

The repository contains a credible pre-production core: provider-neutral payment records, database FSM enforcement, atomic callback/event/outbox writes, asynchronous connectors, provider-head finality, bounded outbox retries, and a real-PostgreSQL concurrency harness. The ordinary local suite passes.

It is not yet a deployable or certifiable live financial service. The quoted activation report overstates several controls and names code/configuration that does not exist. Most importantly:

1. The PostgreSQL integration suite has not run in this environment and is not active in CI.
2. The current GitHub Actions job cannot start because the GitHub account is locked for a billing issue; workflow-file write permission is a separate known blocker.
3. There is no HTTP application, production deployment definition, migration job, worker daemon/process supervisor, external-secret integration, or capability activation flag.
4. There is no suspense case model or operator workflow for unmatched callbacks.
5. The OSCAL implementation emits Assessment Results and POA&M documents validated against project-owned subset schemas, not the official full OSCAL schema; no SSP or Trestle workflow exists.
6. End-to-end PII isolation is not established. Raw callback JSON is persisted, degraded/offline tool arguments can enter checkpointed state, and telemetry has no mandatory central redactor.

No production credentials should be provisioned and no regional adapter should be registered into a live runtime until the P0 exit criteria in this report are demonstrated.

## 2. Evidence collected

| Check | Observed result | Evidence |
|---|---|---|
| Working revision | Clean branch at `e4cdca5` before this audit document | `git status`; `git log` |
| Unit/evaluation suite | `59 passed, 1 skipped in 0.52s` | `.venv/bin/python -m pytest -q` |
| PostgreSQL integration suite | **Not executed**; its module is the one skipped without `AGENTBRIDGE_TEST_POSTGRES_DSN` | `tests/integration/test_payment_concurrency.py` |
| Golden trajectories | `7/7`, 100% expected outcomes | `PATH="$PWD/.venv/bin:$PATH" make eval` |
| Local PostgreSQL runner | Cannot run here: no `docker` or `psql` executable | `scripts/test-postgres.sh`; command discovery |
| Current PR check | **Failure before job start**: “The job was not started because your account is locked due to a billing issue.” | GitHub Actions run `33536630957`, check run `99952423335` |
| PostgreSQL CI service | Draft only; absent from active workflow | `docs/ci/postgres-service.yml`; `.github/workflows/ci.yml` |
| Dependency model | PostgreSQL, connector, and observability dependencies are optional extras | `pyproject.toml` |

The successful `59` count must not be combined with a claim that PostgreSQL concurrency tests passed: the PostgreSQL module was skipped.

## 3. Claim-by-claim verification

### 3.1 Durable payment FSM — **PARTIAL PASS**

Confirmed:

- Python and SQL define the same six named states.
- SQL trigger `enforce_payment_status_transition()` rejects status transitions outside its explicit allowlist.
- `CONFIRMED` and `FAILED` cannot transition to another status.
- The trigger only guards status changes, so benign non-status updates to terminal records remain possible.

Corrections to the quoted report:

- The SQL function is not named `enforce_payment_fsm_transition()`.
- The lifecycle is not strictly linear. `DRAFT → FAILED`, `PENDING_APPROVAL → FAILED`, and `SUBMITTED → CONFIRMED | FAILED` are explicitly allowed; callback receipt is not mandatory when a provider poll establishes finality.
- No real-PostgreSQL test currently attempts every valid and invalid SQL transition. The existing terminal-state test exercises Python `assert_transition()`, while the migration-content test only searches SQL text.

**Required evidence:** a database parity test generated from `ALLOWED_TRANSITIONS` that proves every SQL edge and every rejection, including terminal immutability.

### 3.2 Async PostgreSQL locking — **PASS FOR IMPLEMENTED OPERATIONS; NOT CERTIFIED**

Confirmed:

- `PostgresPaymentRepository` accepts an AsyncConnectionPool-compatible pool.
- Callback ingestion locks a matched payment row with `FOR UPDATE` before changing its state.
- Job leasing uses `FOR UPDATE SKIP LOCKED` inside a transaction.
- The integration harness covers a callback retry storm, webhook/poller serialization, connection-loss rollback, and concurrent job leasing.

Corrections:

- The payment row lock is not the first database operation in callback ingestion. Event insertion/deduplication occurs first, then the payment row is locked. These operations are still in one transaction.
- “FastAPI threads” are not evidenced: the repository has no FastAPI application or route definitions.
- Optimal event-loop behavior and lock-wait performance have not been load-tested.

### 3.3 Database idempotency — **PARTIAL PASS**

Confirmed:

- `(provider, event_id)` is unique for webhook events.
- `(provider, provider_reference)` is unique for payment transactions.
- Replayed event identity is compared using provider reference and payload SHA-256; a changed identity raises an error.
- Duplicate callbacks use `ON CONFLICT DO NOTHING`, then inspect and continue from the durable row. They do not abort the transaction as the quoted report states.

Material gap:

- Migration `002` adds `provider_receipt` and a partial unique index on `(provider, provider_receipt)` for confirmed payments, but the repository never writes `provider_receipt`. `transition()` updates `provider_reference` only. The receipt-level double-fulfilment control therefore exists as DDL but is not wired into reconciliation.
- There is no fulfilment/credit posting subsystem or unique fulfilment event in this repository, so “double-credit prevention” is not demonstrated end to end.

### 3.4 Atomic callback ingestion and outbox — **CODE PASS; RUNTIME EVIDENCE PENDING**

Confirmed:

- Callback evidence insert, matched-row lock, `SUBMITTED → CALLBACK_RECEIVED`, and outbox insert execute in one psycopg transaction.
- Authenticated unmatched callbacks are durably retained and acknowledged generically.
- Duplicate unmatched callbacks can be matched on a later retry.
- Provider network I/O is not performed inside `ingest_webhook()`.

Corrections:

- The table is `payment_webhook_events`, not `callback_evidence`.
- The quoted “strictly under 50ms” claim has no benchmark, SLO test, or production measurement.
- Raw callback payload JSON is stored in the database. Encryption, retention, access control, legal hold, and deletion controls are unspecified.

### 3.5 Outbox worker — **PARTIAL PASS**

Confirmed:

- `OutboxReconciliationWorker.run_once()` exists.
- It queries provider head-end state before assigning finality.
- Unknown/intermediate states are rescheduled.
- Backoff is exponential from 30 seconds and capped at 1,800 seconds.
- Attempt exhaustion transitions rows to `DEAD`.
- Ownership checks prevent a worker from completing or rescheduling a job leased by another worker.

Corrections and gaps:

- This is a one-shot worker class, not a daemon, Celery/Dramatiq integration, deployment, or supervised worker mesh.
- Lease expiry defaults to 60 seconds, not 10 minutes; there is no independent lease-expiry monitor.
- There is a terminal `DEAD` state, not a separate DLQ, and no alert publisher.
- A worker leases up to 25 jobs and processes them serially. Slow provider calls can let later leases expire before their jobs are processed, allowing duplicate provider polls.
- Final payment transition and outbox completion are separate transactions. Recovery is possible, but crash/reclaim behavior at that exact boundary needs a real-database test.
- No production metrics exist for job age, attempts, dead jobs, reclaim count, or finality lag.

### 3.6 Connector readiness — **CODE PASS; LIVE READINESS UNPROVEN**

Confirmed:

- Daraja, Paystack, and MTN MoMo connectors use async transport.
- Amounts use `Decimal` at connector boundaries.
- Provider endpoints come from a static provider/environment allowlist.
- Redirects are disabled.
- Credentials are resolved per call and are not fields in `AgentState`.
- Each connector exposes direct provider status querying.
- MTN MoMo retry identity is deterministic from `idempotency_key_ref`.
- Ambiguous transport/server outcomes remain non-terminal.

Corrections and gaps:

- `registry.py` has no `is_active` field or registration declarations to toggle. A new registry starts empty and therefore fails closed; live capability is enabled by explicitly constructing and registering adapters.
- Connector constructors default to `Environment.PRODUCTION`. This is risky for a “disabled by default” posture even though an empty registry prevents normal engine resolution.
- No sanitized provider contract fixtures, live certification records, callback registration tests, or production account limit evidence are present.
- `HttpxTransport` creates a new client per request; connection pooling, egress proxy policy, mTLS requirements, and CA policy are not deployment-configured.
- Limits are code defaults, not signed/effective-dated deployment policy.

Actual environment variable names produced by `RuntimeSecretsManager` are:

- `DARAJA_CONSUMER_KEY`, `DARAJA_CONSUMER_SECRET`, `DARAJA_PASSKEY`, `DARAJA_SHORTCODE`
- `PAYSTACK_SECRET_KEY`
- `MTN_MOMO_SUBSCRIPTION_KEY`, `MTN_MOMO_API_USER`, `MTN_MOMO_API_KEY`

The `AGENTBRIDGE_DARAJA_*` and `AGENTBRIDGE_MTN_*` names in the quoted plan will not resolve without a different secret resolver.

### 3.7 Webhook transport and identity — **LIBRARY PASS; DEPLOYMENT ABSENT**

Confirmed:

- Paystack HMAC-SHA512 verification uses the exact body and constant-time comparison.
- Shared-token and trusted-proxy SPIFFE verifier implementations exist.
- Payload size is capped at 1 MiB in the framework-neutral handler.
- Daraja callback URL configuration requires HTTPS and an allowlisted host.

Gaps:

- There is no HTTP server, route at `/v1/payments/...`, reverse proxy, certificate configuration, network policy, or provider callback registration in the repository.
- A provider such as Paystack does not present an AgentBridge workload SVID. Provider signature verification and internal workload mTLS are different trust boundaries; requiring both on every public provider callback would reject legitimate provider traffic unless a verified ingress proxy performs the translation.
- `SpiffeProxyVerifier` trusts headers and is safe only if ingress strips caller-supplied identity headers and sets them after mTLS verification. No such proxy configuration is present.

### 3.8 Secrets isolation — **DESIGN PASS; DEPLOYMENT PENDING**

Confirmed:

- No live provider secret literal was found in tracked runtime code.
- Secret values are dynamically resolved at connector execution and wrapped in `SecretStr` before reveal.
- Provider secret objects are not represented in graph state models.

Gaps:

- No Vault, cloud secret manager, External Secrets Operator, CSI driver, workload identity, rotation, revocation, or audit configuration exists.
- No automated test scans checkpoints, traces, logs, exceptions, and OSCAL artifacts for seeded canary secrets.
- `SecretStr` protects representation only; revealed headers remain ordinary strings in memory.

### 3.9 PII and Kenya DPA controls — **FAIL / P0**

Positive evidence:

- Connector payment intents carry tokenized `recipient_ref`, resolved only at the connector boundary.
- Production-trace dataset hydration contains key-based redaction.
- The operator UI uses scrubbed references and warns against raw OTP/PIN entry.

Blocking gaps:

- `payment_webhook_events.payload` persists the full raw provider callback JSONB without a field allowlist, encryption policy, retention policy, or access-control evidence.
- `AgentRouter` stores raw tool `arguments` in `AgentState.fallback_queue` during degraded/offline execution. MCP contracts include a raw `phone` field, so an MSISDN can be serialized by `checkpoint_payload()`.
- Telemetry accepts arbitrary attributes and has no mandatory redaction processor. Circuit-breaker exception text is copied into trace attributes and state.
- There is no gateway-boundary PII classifier/redactor and no external-LLM egress assertion test.
- There is no data inventory, lawful-basis mapping, data-subject workflow, transfer assessment, retention schedule, or Kenya DPA sign-off artifact.

A code scan and unit tests alone cannot certify Kenya Data Protection Act compliance.

### 3.10 OSCAL/GRC — **PARTIAL IMPLEMENTATION; NOT SIGN-OFF READY**

Confirmed:

- Budget breach invokes `export_oscal_results()`.
- Assessment Results and POA&M files are written via temporary-file flush, file `fsync`, and atomic rename.
- Unit tests force a failed budget finding and inspect generated documents.

Corrections and gaps:

- The Assessment Results filename is `assessment-results.oscal.json`, not `ar.oscal.json`.
- Validation uses project-owned **subset** schemas explicitly titled “AgentBridge subset,” not the full official NIST OSCAL 1.2.1 schemas.
- No SSP model, component definition, profile/catalog mapping, Trestle dependency, Trestle workspace, or schema-validation command exists.
- Atomic replacement is implemented, but the parent directory is not `fsync`ed after rename; crash-durability is therefore not fully demonstrated on all POSIX filesystems.
- No concurrent writer/load test exists for a shared `run_id`.
- No NCSC CAF v4 control mapping or collected control evidence exists. The quoted report cannot truthfully claim a CAF v4 audit.

## 4. CI/CD blocker analysis

There are currently two independent blockers:

1. **Repository workflow update blocker:** previous pushes modifying `.github/workflows/ci.yml` were rejected because the connected GitHub App lacks workflow-write permission. The ready service fragment remains in `docs/ci/postgres-service.yml`.
2. **GitHub Actions execution blocker:** the latest PR job did not start because the GitHub account is locked due to a billing issue. This is the current check failure, independent of test correctness.

Both must be cleared. Once GitHub access is repaired, the workflow must install the PostgreSQL extra, start PostgreSQL 16, set `AGENTBRIDGE_TEST_POSTGRES_DSN`, and run the integration module without allowing a skip. A CI command that exits successfully after skipping the integration module is not acceptable activation evidence.

## 5. Corrected activation sequence

### P0 — establish trustworthy evidence

- [ ] Resolve the GitHub billing/account lock and confirm an ordinary workflow job can start.
- [ ] Reconnect/elevate the Arena GitHub App with workflow-write permission.
- [ ] Apply `docs/ci/postgres-service.yml` to `.github/workflows/ci.yml`.
- [ ] Make CI fail if the PostgreSQL integration module skips or collects no tests.
- [ ] Add SQL/Python FSM parity tests for every state pair.
- [ ] Add real-database tests for lease expiry, attempt exhaustion, final-state/complete crash recovery, and receipt uniqueness.
- [ ] Wire verified provider receipts into the ledger and define a unique, idempotent fulfilment boundary.
- [ ] Remove raw tool arguments from checkpointed fallback state; store an encrypted/tokenized job reference instead.
- [ ] Add mandatory telemetry/log/LLM-egress redaction with seeded canary tests.
- [ ] Define callback payload minimization, encryption, retention, and restricted-access controls.

**P0 exit:** green CI with a non-skipped PostgreSQL 16 suite; no seeded secret/MSISDN appears in checkpoints, traces, logs, OSCAL, or model egress; database receipt and fulfilment invariants pass concurrency tests.

### P1 — package a deployable service

- [ ] Implement an authenticated HTTP application around `WebhookHandler` with exact-body signature verification and generic responses.
- [ ] Implement a supervised async worker loop with bounded concurrency, lease renewal/lease sizing, graceful shutdown, DLQ alerting, and metrics.
- [ ] Add a migration job with TLS and least-privilege database roles.
- [ ] Add an explicit deployment-level activation policy; change connector defaults to sandbox or require the environment explicitly.
- [ ] Integrate workload identity and an external secret manager; document exact runtime secret names and rotation.
- [ ] Configure ingress TLS, trusted proxy header stripping, body limits, provider signature boundaries, egress allowlists, and network policy.
- [ ] Add unmatched-payment suspense records, operator authorization, dual control, evidence, and SOPs.

**P1 exit:** a production-like environment passes smoke, failover, restart, replay, and secret-rotation tests with adapters still disabled for money movement.

### P2 — compliance and controlled activation

- [ ] Generate an SSP and validate SSP/AR/POA&M against official OSCAL schemas, preferably through a pinned Trestle toolchain.
- [ ] Map implemented controls to NCSC CAF and applicable Kenya DPA obligations with named owners and evidence links.
- [ ] Complete provider production certification and callback registration.
- [ ] Run disaster recovery, lock contention, provider degradation, callback loss/duplication, and rolling deployment exercises.
- [ ] Activate one corridor with low limits, canary traffic, dual approval, and automatic kill switches.
- [ ] Reconcile against provider settlement reports before expanding traffic.

**P2 exit:** signed security/compliance approval, operational runbooks, demonstrated RPO/RTO and reconciliation SLOs, and successful controlled canary settlement.

## 6. Final verdict

| Area | Verdict |
|---|---|
| FSM and transaction design | Substantially implemented; database parity evidence incomplete |
| Atomic callback ingestion | Implemented; real-PostgreSQL CI evidence blocked |
| Outbox leasing/backoff | Implemented as primitives; daemon, DLQ alerts, metrics, and lease hardening absent |
| Async provider connectors | Implemented and unit-tested; provider/live certification absent |
| Default-off activation | Registry fails closed, but no `is_active` toggle and connectors default to production |
| CI/CD | **Blocked** by account billing lock and workflow-write permission |
| Secrets deployment | Runtime interface exists; production injection/rotation absent |
| Suspense operations | Absent |
| PII/Kenya DPA | **P0 gaps remain** |
| OSCAL | AR/POA&M subset implementation only; SSP/full validation absent |
| Production activation | **NO-GO** |

The next implementation milestone should not be a cosmetic dashboard skeleton. It should close the P0 evidence gaps—especially CI execution, receipt/fulfilment idempotency, checkpoint/telemetry PII isolation, and exhaustive real-database tests—before adding deployment activation or live credentials.
