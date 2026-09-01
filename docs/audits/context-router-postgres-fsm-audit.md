# Audit: Context Router and PostgreSQL Payment FSM Proposal

**Date:** 2026-09-01  
**Scope:** Proposed `ContextProfile` routing schema, PostgreSQL DDL, FSM trigger, and locked reconciliation function  
**Current baseline:** AgentBridge Africa 2.5.0 (`RailRouter`, live capability packs, `PaymentLifecycle`, `PostgresSaver`, atomic callback/FSM/outbox ingestion)

## Executive decision

**Verdict: adopt the safety intent, but do not merge the proposed code verbatim.**

The proposal is directionally aligned with AgentBridge: exact decimal amounts, typed country/currency values, database uniqueness, row locks, terminal-state protection, and webhook-plus-poll reconciliation are all appropriate. However, the example combines profile, payment intent, provider selection, and executable adapter configuration in one mutable Pydantic validator. Its persistence model is M-Pesa-specific, while AgentBridge's MCP interface and ledger are provider-neutral. Its outcome handler also promotes every non-success response to `FAILED`, allowing an unverified callback or a transient/pending result to assign financial finality.

The target design should keep these boundaries:

```text
ContextProfile (tenant/locale policy)
        +
PaymentIntent (amount/currency/corridor/recipient reference)
        +
ProviderHealthSnapshot + server policy
        │
        ▼
RailRouter → immutable RailSelection → capability-pack registry
        │
        ▼
Generic payment_transactions FSM + provider-specific payload vault
```

## Finding matrix

| ID | Proposal | Decision | Severity | Rationale / required adaptation |
|---|---|---|---|---|
| ROUTE-01 | `Decimal` amount with positive bounds | **Accept** | — | Financial amount boundaries should not use binary floating point. Quantize per currency/provider before submission. |
| ROUTE-02 | Country, currency, and rail enums | **Accept with extension** | Medium | The listed values omit supported USD flows and future rails. Use a versioned registry and ISO validation; avoid a release for every new capability pack. |
| ROUTE-03 | Put transaction ID, amount, corridor, routing, and arbitrary metadata in `ContextProfile` | **Reject** | High | A profile is durable tenant/runtime policy; a payment intent is per transaction. Combining them makes profile caching unsafe and weakens audit provenance. Introduce `PaymentIntent`. |
| ROUTE-04 | Mutate `specified_rail` in a Pydantic `model_validator` | **Reject** | High | Validation should validate, not perform health-dependent routing. Mutation hides which inputs produced the decision and cannot account for current gateway availability. Return an immutable `RailSelection` from `RailRouter`. |
| ROUTE-05 | Natural-language caller may force `specified_rail` | **Reject by default** | Critical | This bypasses provider-health, fee, residency, and policy controls. A rail override must be a separately authorized operator policy input, not an MCP payment argument. |
| ROUTE-06 | `adapter_import_path` and `gateway_endpoint` in routing output | **Reject** | Critical | Data-driven import paths create code-loading risk; caller-controlled endpoints create SSRF/credential-exfiltration risk. Resolve a fixed adapter ID through an allowlisted dependency-injection registry. Endpoints come from deployment configuration. |
| ROUTE-07 | KE must always use KES; NG must always use NGN | **Adapt** | Medium | This is safe for domestic-only products but rejects valid cross-border/FX collection corridors. Route against an explicit, compliance-approved corridor matrix rather than universal country rules. |
| ROUTE-08 | Hardcode M-Pesa `150,000 KES` | **Adapt** | High | Provider/product/account limits can change and may differ by operation. Store effective-dated limits in signed policy/configuration and record the policy version in the decision. Keep a platform-wide emergency ceiling at the schema boundary. |
| ROUTE-09 | `metadata: Dict[str, Any]` | **Restrict** | High | Arbitrary metadata can carry PAN, PIN, OTP, secrets, or unbounded data. Use a typed allowlist, size limits, and references to encrypted records. Never include secrets in graph checkpoints or traces. |
| DB-01 | Unique provider request identifier | **Accept** | — | Keep uniqueness scoped by provider because reference domains differ. Current `(provider, provider_reference)` is the correct generic equivalent. |
| DB-02 | Partial unique settled receipt index | **Accept and generalize** | High | Add `(provider, provider_receipt)` uniqueness for confirmed records to prevent duplicate credit/fulfilment across separate requests. |
| DB-03 | M-Pesa-only columns (`shortcode`, `checkout_request_id`, receipt) in core ledger | **Reject** | High | They leak capability-pack details into the unified ledger. Use `provider`, `provider_reference`, and `provider_receipt`; keep provider details in a restricted provider record/event store. |
| DB-04 | Five-state enum (`PENDING`, `COMPLETED`, etc.) | **Reject** | High | It loses approval, submission, callback-observed, and verified-finality distinctions. Keep `DRAFT`, `PENDING_APPROVAL`, `SUBMITTED`, `CALLBACK_RECEIVED`, `CONFIRMED`, `FAILED`; model timeout as reconciliation metadata or a non-terminal state with explicit transitions. |
| DB-05 | Trigger blocks updates from `COMPLETED` unconditionally | **Adapt** | Medium | It also blocks benign audit/settlement metadata updates. Enforce immutability only when `OLD.status IS DISTINCT FROM NEW.status`; protect financial columns separately. |
| DB-06 | Trigger's transition rules | **Reject as incomplete** | Critical | `FAILED → TIMED_OUT`, `PENDING → UNKNOWN`, and other unintended transitions remain possible. Encode an explicit allowlist matching application FSM transitions. |
| DB-07 | `SELECT ... FOR UPDATE` | **Accept** | — | Correct for short state transitions. Never hold the lock while making a provider network request or waiting for human approval. |
| DB-08 | New synchronous `psycopg2.connect` per callback | **Reject** | High | It blocks async workers and creates connection churn. Use the existing psycopg 3 async pool through `PostgresPaymentRepository`. |
| DB-09 | Treat every provider status except `SUCCESS` as `FAILED` | **Reject** | Critical | `PENDING`, `PROCESSING`, `UNKNOWN`, transport errors, and malformed responses are not failures. Only an authenticated provider head-end query may map explicit terminal statuses. |
| DB-10 | Callback directly invokes final outcome processing | **Reject** | Critical | A callback is an occurrence hint. Persist/deduplicate it, transition to `CALLBACK_RECEIVED`, commit, then queue head-end reconciliation. Current `WebhookHandler`/`PaymentReconciler` follows this separation. |
| DB-11 | Roll back and dismiss an unknown callback | **Adapt** | Medium | Authenticated orphan events should be retained for delayed matching and incident review. Return a generic 2xx to avoid retries without disclosing transaction existence. |
| DB-12 | Merge raw callback JSON into transaction metadata | **Reject** | High | It creates uncontrolled PII growth and makes the ledger mutable. Store immutable webhook events in a restricted/retention-managed table; expose only normalized fields and hashes to graph state. |
| DB-13 | `logger.*(f"...{provider data}")` | **Reject** | Medium | Structured logs should use opaque internal IDs and redaction. Do not log receipts, MSISDNs, payloads, or exception text that may contain provider responses. |
| DB-14 | `raise e` | **Reject** | Low | Use bare `raise` to preserve the original traceback. Map known database errors to typed internal errors at the API boundary. |
| DB-15 | Current callback event insert and transaction transition occur in separate database transactions | **Remediate** | Critical | A crash after event commit but before state transition makes the retry look duplicate and skip transition. Atomically record the event, advance the FSM, and enqueue an outbox reconciliation item for matched transactions. |
| PERSIST-01 | Describe `PostgresSaver` as the payment lock manager | **Clarify** | High | `PostgresSaver` persists LangGraph checkpoints. Payment idempotency and row locks belong to the payment repository/ledger. They may share PostgreSQL infrastructure, but they have separate transaction semantics and schemas. |

## Conflicts with the current architecture

### 1. Profile versus intent

Current `agentbridge.core.state.ContextProfile` represents locale, supported rails, connectivity, cost ceilings, AML limits, and HITL policy. The proposal would turn it into a transaction object. This conflicts with the existing versioned public state contract and would make a profile impossible to reuse safely.

**Action:** retain `ContextProfile`; introduce a strict `PaymentIntent` with `Decimal`, currency, source/destination, recipient reference, and idempotency reference. Persist both in `AgentState`, but hash or tokenize sensitive recipient data.

### 2. Deterministic routing versus validation side effects

Current `RailRouter.select()` already combines profile, currency, destination, configured rails, and provider health to return `RailSelection`. Moving rail selection into `model_validator` would remove health-aware failover and make validation environment-dependent.

**Action:** strengthen `RailRouter` with a versioned corridor/limit policy. Keep `RailSelection` immutable and record policy version, health snapshot ID, and rationale.

### 3. Generic ledger versus M-Pesa ledger

Current `payment_transactions` supports multiple providers. The proposed table is useful as a capability-pack projection but not as the system-of-record ledger.

**Action:** keep the generic ledger. Add provider-specific normalized tables only when a connector requires fields that do not belong in the core model.

### 4. Finality semantics

Current webhook handling intentionally stops at `CALLBACK_RECEIVED`; `PaymentReconciler` polls the provider before a final state. The proposed `process_payment_outcome` can move directly from a callback payload to `COMPLETED`/`FAILED` and maps all non-success values to failure.

**Action:** retain the current two-stage design. Provider capability packs own explicit status mappings; unknown/pending values remain non-terminal.

## Action plan

### P0 — before any live credentials or money movement

- [ ] Add `PaymentIntent` and typed `Money` models using `Decimal`; prohibit floats at the provider boundary.
- [ ] Add an allowlisted `CapabilityPackRegistry`; remove all runtime import-path and endpoint selection from transaction data.
- [ ] Make manual rail override an operator-only policy object requiring `admin:rail-override`, justification, and audit evidence.
- [x] Add a database transition trigger with an explicit allowlist matching `ALLOWED_TRANSITIONS`.
- [x] Add generic `provider_receipt` and a partial unique index for confirmed transactions.
- [x] Add `retry_count`, `max_retries`, `next_reconcile_at`, and `last_reconcile_error_code`; never classify transport errors as financial failure.
- [x] Return generic HTTP 200 for authenticated unmatched callbacks after durable event storage.
- [x] Add an atomic repository operation that records the callback, transitions matched state, and inserts an outbox reconciliation job in one transaction; duplicate retries repair/read the existing outcome rather than exiting early.
- [x] Add a leased `SKIP LOCKED` outbox worker so provider polling starts only after the callback transaction commits.
- [x] Add migration and concurrency tests against PostgreSQL 16 in CI, plus a local container harness.

**Exit criteria:** concurrent duplicate webhook and poll workers produce one final ledger transition and one fulfilment event; an unverified callback can never produce `CONFIRMED`.

### P1 — connector readiness

- [ ] Implement `ke-payments`, `ng-payments`, and `gh-payments` as registered capability packs implementing one async adapter protocol.
- [ ] Define explicit provider status maps with `PENDING`, success-terminal, failure-terminal, and unknown buckets.
- [ ] Move limits to effective-dated policy configuration and record `policy_version` on each payment.
- [ ] Add circuit health, fee quote, data-residency, and capability checks to rail scoring.
- [ ] Add webhook event encryption/retention controls and structured redacted logging.
- [ ] Add a signed settlement-import interface and discrepancy findings for nightly reconciliation.

**Exit criteria:** contract tests replay sanitized provider fixtures for initiation, duplicate callbacks, delayed callbacks, unknown statuses, polling recovery, and settlement mismatch.

### P2 — operational hardening

- [ ] Add PostgreSQL lock-contention, deadlock-retry, failover, and rolling-deployment tests.
- [ ] Add transaction-level metrics: callback lag, reconciliation lag, orphan rate, duplicate rate, lock wait, and finality age.
- [ ] Generate OSCAL findings when retry budgets, settlement assertions, policy overrides, or reconciliation SLAs fail.
- [ ] Add regional data-retention policies and deletion/legal-hold workflows.
- [ ] Run fault injection for callback loss, duplicated delivery, provider timeout, stale health snapshots, and partial database outage.

**Exit criteria:** recovery-point/recovery-time objectives, reconciliation SLAs, and zero-double-fulfilment invariants are demonstrated in a production-like environment.

## Recommended target models

Do not expose these policy fields as free-form MCP arguments:

```python
class PaymentIntent(BaseModel):
    intent_id: UUID
    amount: Decimal
    currency: Currency
    source_country: Country
    destination_country: Country
    recipient_ref: str       # token/reference, not raw PIN/OTP/PAN
    idempotency_key_ref: str

class RailOverride(BaseModel):
    rail: PaymentRail
    operator_subject: str
    authorization_reference: str
    justification: str

class RailSelection(BaseModel):
    adapter_id: str          # allowlisted registry key, never import path
    provider: str
    rail: PaymentRail
    policy_version: str
    health_snapshot_id: str
    rationale_codes: list[str]
```

The adapter registry resolves `adapter_id` to an injected implementation and deployment-owned endpoint. This preserves the invariant that probabilistic/model-controlled data cannot select executable code or arbitrary network destinations.

## Required database transition map

The database trigger and Python `ALLOWED_TRANSITIONS` must be generated from or tested against the same table:

```text
DRAFT             → PENDING_APPROVAL | FAILED
PENDING_APPROVAL  → SUBMITTED | FAILED
SUBMITTED         → CALLBACK_RECEIVED | CONFIRMED | FAILED
CALLBACK_RECEIVED → CONFIRMED | FAILED
CONFIRMED         → (no status transition)
FAILED            → (no status transition)
```

If late success after timeout is a business requirement, represent timeout as non-terminal reconciliation state/metadata and define the exact evidence required for transition. Do not allow a generic resurrection from `FAILED`.

## Final recommendation

Merge the proposal as an **architectural input**, not as a replacement implementation. Preserve AgentBridge's provider-neutral router and two-stage callback finality. Incorporate Decimal money, stronger typed intents, database-enforced transition parity, partial receipt uniqueness, and lock-contention testing through the P0 plan above.
