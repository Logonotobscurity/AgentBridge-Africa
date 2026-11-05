# Trace and telemetry data-minimization policy

**Policy version:** 2026-11-05.v1  
**Implementation:** \`agentbridge/core/sensitive_data.py\`  
**Decision:** minimize before observability/evaluation persistence; fail closed for unclassified text.

## Why this policy exists

The trace hydrator previously used a small exact-key list and deterministic, unkeyed SHA-256 fragments. That approach missed naming variants and made stable, linkable digests look like anonymization. The trace path also copied span attributes and Langfuse input/output into evaluation records before the JSONL and dataset sinks.

The policy follows three external design principles:

1. **Contextual PII protection.** NIST SP 800-122 frames PII protection around the context and impact of each data item, rather than a one-size-fits-all list. This project therefore classifies fields and explicitly limits what evaluation datasets retain. See [NIST SP 800-122](https://csrc.nist.gov/pubs/sp/800/122/final).
2. **LLM sensitive-information disclosure controls.** OWASP's 2026 LLM Top 10 identifies sensitive-information disclosure as a first-class LLM application risk. Its mitigation guidance emphasizes data minimization, sanitization, redacting traces, and restricting prompt/output logging. See [OWASP GenAI LLM Top 10 2026](https://github.com/GenAI-Security-Project/GenAI-LLM-Top10/tree/main/2026) and [LLM02 working guidance](https://github.com/GenAI-Security-Project/GenAI-LLM-Top10/blob/main/2026/working/LLM02_SensitiveInformationDisclosure.md).
3. **Telemetry restraint.** OpenTelemetry advises collecting only attributes that serve an observability purpose and treating sensitive/verbose attributes as opt-in. It also places responsibility for sensitive-data handling on the instrumentation owner. See [OpenTelemetry sensitive-data guidance](https://opentelemetry.io/docs/security/handling-sensitive-data/) and [semantic-convention authoring guidance](https://opentelemetry.io/docs/specs/semconv/how-to-write-conventions/).

## Policy rules

- Normalize keys by case-folding and removing punctuation. This covers common snake_case, camelCase, punctuation and case variations.
- Redact known sensitive keys and containers, including BVN/NIN, government identifiers, account/bank details, payment credentials, customer/beneficiary/recipient identity, contact fields and addresses.
- Omit free-text fields such as prompts, context, rationale, messages, notes, descriptions, callback bodies, transcripts, errors and reasoning.
- Omit unclassified string values and unclassified numeric values by default. Preserve only controlled enums, validated opaque trace identifiers, booleans and explicitly allowlisted operational numeric fields.
- Retain only the small set of tool names, provider/currency/status values and span names defined in the policy code. Unknown values are not silently passed through.
- Do not compute deterministic hashes of PII. A retained trace/span identifier is pseudonymous and linkable, not anonymous. It is retained only for evidence lineage, and still requires access control and a retention limit.
- Apply the same policy when creating telemetry spans, converting them to SpanRecord/OTLP, hydrating OTEL or Langfuse records, writing JSONL, and publishing Langfuse dataset items.

## What is retained

The current allowlist is intentionally narrow:

- Payment amount, cost, retry counts, bounded durations/latencies, evaluation scores and selected status codes.
- Enumerated tool/provider/rail/currency/country/status/decision/event values.
- Valid opaque trace/span IDs in supported UUID/hex/ULID forms, for trace lineage only.
- Boolean policy outcomes and explicit policy version.

This is not a general-purpose PII detector. The absence of a key match is not a guarantee that an arbitrary encoded or transformed secret is safe; unknown text is therefore dropped rather than scanned and retained.

## Coverage and limitations

| Boundary | Covered by this change | Evidence / limitation |
|---|---|---|
| In-process custom Tracer span attributes | Yes | Minimized on start and again at record/export boundaries |
| SpanRecord conversion | Yes | Uses minimized attributes, span name and validated IDs |
| Custom OTLP JSON export | Yes | Span attributes are minimized before serialization |
| Optional OpenTelemetry SDK span attributes | Yes | Only minimized scalar/primitive-array attributes are emitted |
| OTEL trace hydration | Yes | Nested mapping/list payloads and attribute arrays covered by tests |
| Langfuse input/output hydration | Yes | Prompt-like fields and unclassified strings omitted |
| EvalCase JSONL | Yes | Policy reapplied immediately before write |
| Langfuse dataset publication | Yes | Policy reapplied immediately before client call; tests use a recording fake |
| Webhook callback persistence | Separate existing control | Provider-specific allowlisted projection and keyed HMAC fingerprints; not changed here |
| LangGraph checkpoints / AgentState / fallback queue | **Not established** | Must be separately inspected and canary-tested before claiming end-to-end isolation |
| General application logs and third-party SDK auto-instrumentation | **Not established** | Must be inventoried and tested separately |
| External Langfuse storage, retention and access controls | **Not established** | Requires configuration/contract review and account-level verification |

## Acceptance evidence for this patch

- Key-normalization tests cover Nigerian identity and banking aliases.
- Free-text and unknown-string canaries are absent from sanitized output.
- OTEL attribute canaries are absent from both SpanRecord and custom OTLP output.
- Langfuse input/output canaries are absent from hydrated records.
- JSONL and dataset-publisher tests reapply the policy at their respective sinks.
- Tests prove known safe numeric/enumerated evidence remains available.
- No result is described as anonymization or end-to-end PII protection.

**Operational decision remains NO-GO for live money movement.** This patch narrows trace/telemetry exposure; it does not establish checkpoint, general-log, callback-store or production deployment readiness.
