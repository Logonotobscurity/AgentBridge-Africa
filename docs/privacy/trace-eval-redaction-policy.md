# Trace-to-Eval Redaction Policy

**Policy ID:** LOGON-AB-PRIV-001  
**Version:** 1.0.0  
**Implementation:** `evals/trace_hydrator.py`  
**Status:** Implemented in the trace-hydrator branch; end-to-end privacy assurance remains incomplete.

## 1. Purpose and boundary

This policy governs data copied by the OTEL/Langfuse trace hydrator into AgentBridge `EvalCase` objects, JSONL evaluation files, and Langfuse dataset items created from those cases.

It is not a universal data-loss-prevention control. It does **not** establish that sensitive values are absent from application logs, graph state/checkpoints, callback persistence, raw OTEL exporters, Langfuse ingestion before hydration, exception telemetry, backups, or other stores. Those boundaries require separate inventory, configuration, and seeded-canary verification.

## 2. Policy behavior

### Retain

The hydrator retains evaluation structure needed for offline scoring, including registered tool names, non-sensitive argument values, non-sensitive expected-output fields, score fields, and opaque source trace/observation identifiers used for lineage.

Opaque trace and observation identifiers are **pseudonymous and linkable**, not anonymized. They require access controls, retention limits, and an operational need.

### Replace with a fixed marker

Known sensitive values are replaced with the literal `[REDACTED]`. Matching splits camelCase and recognizes punctuation/case variants, then checks sensitive tokens and phrases.

The current alias policy covers, among other fields:

- BVN, NIN, national/identity numbers, passport and driver-license identifiers;
- bank/account numbers, IBAN, SWIFT, routing/sort codes, card numbers, CVV/CVC, PIN and OTP;
- access/refresh tokens, authorization values, secrets and passwords;
- email, phone/mobile/MSISDN, addresses, recipient, beneficiary and customer identifiers/names;
- common full/first/last/given/family-name and date-of-birth forms.

The key policy is intentionally conservative. New provider-specific aliases must be added with regression tests; the list is not a complete detector for all PII.

### Drop

Fields known to carry free text or raw payloads are omitted rather than passed through a key-based sanitizer. This includes prompt, context, rationale, message, notes, description, body/callback body, raw payload, content, transcript, query, request/response body, exception detail and tag fields.

Dynamic span labels are not emitted as tool names. Only tool identifiers that exactly match the registered tool catalogue are retained.

### Never hash as a substitute for removal

The previous truncated SHA-256 replacement was removed. A deterministic digest remains linkable and can be guessed for low-entropy identifiers; it must not be described as anonymization. Where linkage is genuinely necessary, use a separately governed pseudonymization design with access control and a defined retention period.

## 3. Test requirements

The regression suite seeds distinctive canaries for identity, banking, customer/beneficiary, phone, token, and free-text values. It verifies their absence from:
- recursively hydrated nested OTEL arguments, including list elements;
- Langfuse input and output cases;
- serialized JSONL files;
- the exact payload passed to the Langfuse dataset publication client.

The tests also assert that useful non-sensitive structure remains and that punctuation/camelCase variants are matched.

These are hydrator-surface tests only. They do not certify external telemetry pipelines or persistence layers.

## 4. Required controls outside this module

Before any production or live-payment readiness claim, separately test and enforce the same data-minimization policy at every actual sink and egress boundary:

1. Graph state and checkpoint serialization, including fallback queues and exception state.
2. Callback ingestion and database persistence, including raw JSONB, backups and operator exports.
3. Application logs and exception reporting.
4. OTEL SDK/exporters and Collector pipelines before any vendor exporter.
5. Langfuse trace ingestion, dataset creation, and all export/download paths.
6. OSCAL/evidence artifacts, CI logs, support bundles and debugging captures.

Use a single governed attribute allowlist where possible. OpenTelemetry documents processors for transforming/filtering telemetry and a redaction processor that can remove attributes outside an allowlist; the processor must be deployed and verified for each configured pipeline, not merely assumed to exist.

## 5. Research basis and limitations

- NIST SP 800-122 recommends context-based identification and safeguards for PII confidentiality; it is guidance, not an implementation-specific certification.
- Nigeria's Data Protection Commission describes minimization, storage limitation, and security safeguards as core data-protection principles. This code change alone does not establish compliance with the Nigeria Data Protection Act or any other law.
- OpenTelemetry's sensitive-data guidance warns that hashing user identifiers may not provide the desired anonymization and describes attribute deletion, filtering, redaction and transform processors.
- OAuth scope evidence remains a separate authorization-evaluation concern; OAuth 2.0 Security BCP recommends restricting token privileges to the minimum needed. Hydrator redaction does not establish runtime authorization.

## 6. Operational ownership

Any change to aliases, drop fields, retained identifiers, or dataset publication behavior must:
- increment `REDACTION_POLICY_VERSION` when semantics change;
- add seeded regression canaries for the new field/provider;
- identify which persistence/export surfaces the change covers;
- explicitly document any retained pseudonymous linkage;
- avoid a whole-system privacy claim until all sinks have evidence.

## References

- NIST SP 800-122: https://csrc.nist.gov/pubs/sp/800/122/final
- Nigeria Data Protection Commission, data privacy principles: https://ndpc.gov.ng/our-data-privacy-policy/
- OpenTelemetry, handling sensitive data: https://opentelemetry.io/docs/security/handling-sensitive-data/
- OpenTelemetry Collector processors: https://opentelemetry.io/docs/collector/components/processor/
- IETF RFC 9700, OAuth 2.0 Security Best Current Practice: https://www.rfc-editor.org/rfc/rfc9700.html
