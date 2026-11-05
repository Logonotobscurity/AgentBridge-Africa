# Trace-to-Evaluation Evidence Coverage

**Status:** engineering audit / not a production certification
**Audited base:** `arena/01a05d72-agentbridge-africa`
**Related change:** PR #4, fail-closed destructive-scope evaluation
**Purpose:** document the existing evidence path and its current blind spots before adding another telemetry abstraction.

## Executive finding

AgentBridge already has a trace-to-evaluation path:

`Tracer / SpanRecord → OTLP-shaped export or Langfuse trace → trace_hydrator → EvalCase JSONL → production_sampler scoring → golden harness / OSCAL evidence`

The path is not yet a complete execution-evidence chain. It can score a subset of tool-call metadata, but it does not consistently prove that an action was authorized, approved, dispatched, accepted by a provider, reconciled to a terminal state, and safely persisted. A trace is evidence of an observation, not proof that an external side effect succeeded.

## Component map

| Component | Current responsibility | Evidence boundary / limitation |
|---|---|---|
| `agentbridge/core/telemetry.py` | In-memory spans; OTLP-shaped JSON export; optional OpenTelemetry bridge | Attributes are stringified in OTLP output; no mandatory central redaction; optional SDK bridge starts and immediately ends a separate span |
| `agentbridge/core/state.py` | Versioned `AgentState`, OAuth scope list, payment lifecycle, checkpoint and trace records | State model contains fields but does not itself prove the fields were populated from trusted authorization/provider evidence |
| `evals/trace_hydrator.py::from_otel` | Reads normalized spans or OTLP `resourceSpans`; extracts tool, args, OAuth scopes, retry fields; scores and redacts EvalCase input | Missing scope currently normalizes to empty and should fail closed; field names must match emitted span attributes |
| `evals/trace_hydrator.py::from_langfuse` | Reads trace input/output and metadata; extracts scope and retry metadata; scores and redacts input/output | Requires a documented metadata contract; provider status, approval event, dispatch state and reconciliation state are not currently part of the normalized record |
| `evals/production_sampler.py` | Scores required tool arguments, destructive-tool idempotency, scope and retry behavior | A score is only as trustworthy as the source fields; unknown tool names and incomplete lifecycle events need explicit treatment |
| `evals/harness.py` | Replays synthetic golden trajectories and records aggregate results | Exercises local orchestrator trajectories; it is not a production trace replay engine and does not prove provider-side settlement |
| OSCAL exporter | Produces project-shaped assessment/POA&M outputs | Existing activation audit says schemas are project subsets, not official full OSCAL validation |

## Evidence that must be distinguished

A future normalized event contract must not collapse these states:

1. `proposed` — a plan or intent was generated.
2. `policy_checked` — deterministic policy evaluated the action.
3. `authorized` — trusted identity/scope evidence permitted the action.
4. `approval_requested` — human approval was requested.
5. `approved` / `rejected` — authenticated approval decision with actor, time and decision reference.
6. `dispatched` — request was sent to a provider/connector.
7. `provider_accepted` — provider acknowledged receipt; this is not settlement.
8. `pending` / `unknown` — final outcome is not yet established.
9. `succeeded` / `failed` / `reversed` — terminal state supported by provider reconciliation evidence.

Never infer `succeeded` from a generated plan, tool invocation, HTTP 2xx, callback receipt, local mock, or provider acceptance alone.

## Current gaps requiring tests

### Authorization and approval

- Destructive trace scoring must fail closed when scope evidence is absent, malformed, empty, or does not contain the exact required scope.
- The evaluator currently checks `payments:execute` as a shared literal. Confirm whether each destructive tool needs a tool-specific scope mapping before broadening this rule.
- A trace-level scope field does not independently prove token issuer, audience, subject, expiry, tenant binding, or that the scope was checked at dispatch time.
- Approval evidence is not currently represented in the production sampler's normalized contract. Add only after identifying the authoritative runtime approval event and its provenance.

### Lifecycle and outcome

- OTEL and Langfuse hydration do not yet share a fully specified lifecycle-event contract.
- Provider acceptance, callback receipt and reconciled finality must remain distinct.
- Missing or conflicting provider references/statuses must yield `unknown`, not success.
- Retry counters alone do not prove idempotent duplicate handling or safe retry semantics.

### Privacy

- The hydrator's current sensitive-key set is narrow: account number, authorization, email, OTP, phone, PIN, recipient and common token keys.
- Review coverage for BVN/NIN, bank details, customer identifiers, names, addresses, raw callback payloads, free-text rationale, prompt/context content and nested provider response fields.
- Redaction in the hydrator does not protect data already written to application logs, checkpoints, callback persistence, telemetry exporters or external observability collectors.
- Deterministic hash replacement is pseudonymization, not anonymization. Treat hashes as linkable data and document retention/access requirements.
- Add seeded canaries and inspect every persisted/exported surface; do not assert privacy based only on a unit test of the redaction helper.

## Minimal next implementation sequence

1. Add contract tests for OTEL and Langfuse field normalization, including absent, malformed and conflicting authorization evidence.
2. Establish one explicit normalized evidence model with source provenance and an `unknown` outcome; do not add another tracing SDK or storage subsystem.
3. Add seeded PII canary tests at each persistence/export boundary.
4. Add lifecycle tests proving that provider acceptance and callback receipt cannot be scored as settlement.
5. Add tests linking approval evidence to the exact action arguments and idempotency key that were approved.
6. Refresh the activation audit only after the PostgreSQL integration suite and current CI are executable.

## Verification record

- PR #4 adds fail-closed scope handling to the production sampler and regression tests.
- The associated GitHub Actions run was retried. Both attempts ended before any workflow steps executed; no Python test results were produced.
- Do not mark the PR verified or merge it on the basis of a workflow startup failure.
- The pre-existing activation audit records **NO-GO for live money movement**. This document does not change that decision.

## Decision

Do not build more orchestration or telemetry features yet. First prove the existing authorization, approval, privacy and payment-finality evidence from source event through evaluation artifact.