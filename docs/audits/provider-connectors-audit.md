# Audit and action: proposed live provider connectors

**Date:** 2026-09-01

**Providers:** Safaricom Daraja STK, Paystack Transaction API, MTN MoMo Collection

**Decision:** Rework rather than merge verbatim

## Executive finding

The proposed connector direction is aligned with the AgentBridge capability-pack architecture, but several security claims are stronger than the implementation guarantees. The synchronous `requests` design, mutable/generated MTN request IDs, hardcoded policy limits, and assumptions about `SecretStr` conflict with the audited production invariants. The corrected implementation is under `agentbridge.payments` and uses an injected asynchronous transport, per-call secret resolution, immutable exact-decimal intents, deployment-owned URL allowlists, and deterministic provider references.

## Compliance matrix

| Area | Proposed approach | Verdict | Production action |
|---|---|---|---|
| Secret resolution | Read environment variables at execution time into `SecretStr` | **Adapt** | `RuntimeSecretsManager` supports environment resolution but is injected through `SecretResolver`, allowing Vault/KMS/workload-identity implementations. Secret objects never enter `AgentState`. |
| `SecretStr` guarantees | Automatically prevents all logging, exception, and OTEL leaks | **Incorrect claim** | `SecretStr` redacts Pydantic representation only. Revealed values are ordinary strings. Code must never log headers, request bodies, secret-bearing exceptions, or resolved values. |
| HTTP execution | Synchronous `requests` | **Reject** | `HttpxTransport` uses `httpx.AsyncClient`, explicit timeouts, and `follow_redirects=False`. Tests inject a transport without network access. |
| Endpoint security | Static provider URL dictionary | **Accept with hardening** | `ALLOWLIST_ENDPOINTS` is deployment-owned. Callers cannot submit endpoints. Redirect following is disabled, references are path-escaped, and Daraja callback hosts are separately allowlisted. |
| Monetary values | `Decimal` bounds | **Accept** | `PaymentIntent` rejects binary floats. Provider-specific minor-unit/quantization conversion occurs only at the adapter boundary. Limits are injectable, versioned policies rather than hidden validator behavior. |
| Kenya phone model | Override Pydantic `__init__` | **Reject** | Normalize through a pure boundary function before network I/O. Raw OTP/PIN values remain prohibited. |
| Provider output | Normalized immutable result | **Accept** | `ConnectorResult` excludes raw payloads and uses `SUBMITTED`, `PENDING`, `CONFIRMED`, or `FAILED` with reconciliation metadata. Raw responses belong in restricted event storage. |
| Timeout handling | Map all transport exceptions to `PENDING` | **Adapt** | Only redacted transport failures and 5xx initiation ambiguity become non-terminal with `requires_reconciliation=True`. Validation/configuration errors raise before dispatch; explicit 4xx rejections may fail initiation. |
| Finality | Connector status query is source of truth | **Accept** | Query methods map only documented terminal statuses to `CONFIRMED`/`FAILED`; unknown values remain `PENDING`. `PaymentReconciler` accepts normalized connector results. |
| MTN idempotency | Generate UUID inside each initiation | **Reject** | Derive `X-Reference-Id` deterministically with UUIDv5 from `idempotency_key_ref`, preserving identity across retries and restarts. |
| Adapter loading | Dynamic import path | **Reject** | `CapabilityPackRegistry` accepts only fixed IDs (`ke-payments`, `ng-payments`, `gh-payments`, `ug-payments`). No transaction data becomes executable code. |
| Provider failover | Re-resolve provider during retry | **Reject** | Pin the selected capability pack/reference for an intent. Failover creates a new intent under explicit policy; a reference from one provider is never sent to another. |

## Implemented structure

```text
agentbridge/payments/
├── models.py       # PaymentIntent, exact Decimal, ConnectorResult
├── runtime.py      # secret resolver, URL allowlist, async transport, limit policy
├── engine.py       # ContextProfile routing into pinned capability packs
├── base.py         # provider-neutral async protocol
├── daraja.py       # OAuth, STK initiation, STK query, KE normalization
├── paystack.py     # transaction initialization and verification
├── mtn_momo.py     # request-to-pay, status query, deterministic X-Reference-Id
└── registry.py     # allowlisted capability-pack dependency injection
```

## Important limitations before real traffic

1. The built-in environment resolver is a deployment adapter, not a full zero-trust secret system. Production should inject Vault, cloud secret manager, or workload-identity-backed resolution.
2. Default amount limits are bootstrap policy values. Operations/compliance must provide effective-dated, signed policy values and verify current provider/product/account limits.
3. The Paystack pack currently models transaction collection, not transfer-recipient disbursement. Disbursement must be a separate destructive MCP primitive and connector method.
4. Live TLS trust, egress proxy policy, certificate rotation, provider fixture validation, and network fault tests require a production-like environment.
5. Connector `FAILED` means explicit initiation rejection or verified terminal provider failure; it must not automatically trigger fulfilment reversal without ledger policy.
6. No live credentials are included, requested, or committed.

## Action plan

### P0 — connector contract safety

- [x] Replace `requests` with an injected async transport.
- [x] Reject float amounts and preserve `Decimal` until provider conversion.
- [x] Disable redirects and prohibit caller-controlled base URLs/callback hosts.
- [x] Resolve secrets per operation and keep secret objects out of graph state.
- [x] Resolve tokenized recipients only at connector execution; keep raw MSISDN/email out of checkpoints.
- [x] Normalize transport ambiguity to non-terminal reconciliation-required output.
- [x] Derive MTN reference IDs deterministically.
- [x] Add allowlisted capability-pack registration.
- [x] Add unit tests for URL selection, redaction, amount types, normalization, ambiguity, and retry identity.
- [ ] Replace bootstrap limit values with signed/effective-dated production policy configuration.
- [ ] Add a cloud/Vault secret resolver and rotation tests.

### P1 — provider certification

- [ ] Validate request/response mappings against current provider sandbox fixtures and contracts.
- [ ] Add Paystack transfer/disbursement as a distinct connector capability.
- [ ] Add MTN target-environment configuration per subscribed market/product.
- [ ] Add Daraja clock-skew, token-cache, callback-correlation, and query-code fixtures.
- [ ] Add provider-specific retry classifications; never automatically retry an ambiguous POST under a new reference.
- [ ] Run contract tests through controlled egress with TLS verification and DNS/redirect attack cases.

### P2 — operational activation

- [ ] Provision secret-manager references and short-lived workload identities.
- [ ] Register capability packs through deployment dependency injection.
- [x] Bind normalized connector results to the atomic callback/FSM/outbox reconciliation workflow.
- [ ] Add per-provider circuit, latency, reconciliation-age, and error-code telemetry without payloads.
- [ ] Complete provider sandbox certification, limited canary traffic, settlement reconciliation, and rollback drills.

## Activation gate

Live credentials must not be provisioned until all of the following hold:

- the atomic webhook/FSM/outbox P0 from the payment lifecycle audit is complete;
- provider sandbox fixtures pass;
- secret-manager and egress policies are deployed;
- ambiguous initiation retries preserve provider reference identity;
- no raw secret, MSISDN, email, or provider payload reaches OTEL, Langfuse, OSCAL, or LangGraph checkpoints.
