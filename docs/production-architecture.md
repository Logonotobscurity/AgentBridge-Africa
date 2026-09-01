# Production payment architecture

## Research basis

AgentBridge combines patterns that appear independently across mature agent and payment systems:

- [Africa Payments MCP](https://github.com/kenyaclaw/africa-payments-mcp) demonstrates one MCP-facing interface over M-Pesa, Paystack, and MTN MoMo instead of exposing gateway-specific language to the agent.
- [MCP ToolAnnotations](https://modelcontextprotocol.io/specification/2025-06-18/schema#toolannotations) standardize `readOnlyHint`, `destructiveHint`, `idempotentHint`, and `openWorldHint`. The specification calls these hints, not trusted authorization controls, so AgentBridge enforces equivalent server-side policy.
- [M-Pesa MCP](https://github.com/gabrielmahia/mpesa-mcp) separates STK/status and money-moving operations. Daraja STK initiation is asynchronous: the synchronous response supplies a request identifier and the eventual result arrives by callback or status query.
- [Paystack payment verification](https://paystack.com/docs/payments/verify-payments/) recommends webhooks and server-side verification, with duplicate-fulfilment protection before delivering value.
- [MTN MoMo](https://momodeveloper.mtn.com/) exposes request-payment, payment-status, transfer, transfer-status, and balance operations—another initiate/pending/reconcile lifecycle.
- [LangGraph persistence guidance](https://docs.langchain.com/oss/python/langgraph/add-memory) recommends `PostgresSaver` for production and requires one-time saver setup/migrations. SQLite savers are not production persistence.
- [Langfuse datasets](https://langfuse.com/docs/evaluation/experiments/datasets) can create dataset items linked to source trace and observation IDs. Langfuse also recommends masking sensitive OTEL span attributes before export.

## Runtime topology

```text
Natural-language intent
        │
        ▼
Unified MCP contract ── schema + untrusted safety hints
        │
        ▼
Server-side gates ── OAuth scope ── AML/limits ── idempotency ── HITL
        │                                                     │
        │ destructive action                                 └─ OTP/PIN/OAuth verifier
        ▼                                                          stores reference only
ContextProfile + currency + destination + provider health
        │
        ▼
RailRouter ── M-Pesa ── Paystack ── MTN MoMo ── bank/USSD fallback
        │
        ▼
Postgres-checkpointed lifecycle
 created → awaiting_confirmation → submitted → pending_callback
                                      │              │
                                      └──── webhook/status reconciliation
                                                     │
                                            succeeded / failed / reversed
        │
        ├─ OTEL/Langfuse trace → privacy scrub → EvalCase → regression scoring
        └─ OSCAL Assessment Results + POA&M
```

## 1. Unified rail switching

`agentbridge.core.rail_switch.RailRouter` makes provider selection deterministic. Inputs are transaction currency, destination country, `ContextProfile.payment_rails`, and a health snapshot. An unavailable preferred gateway falls through to the next configured rail; no usable rail raises `RailUnavailableError` and fails closed.

`agentbridge.tools.payment_engine.UnifiedPaymentEngine` is the provider-neutral facade. The `process_payment` MCP schema does not accept a provider argument, preventing the model from bypassing routing policy. Once a provider returns a quote/reference, that `RailSelection` is pinned for the intent; failover must create a new intent rather than submitting one provider's reference to another.

Production health should come from circuit-breaker/readiness data with a short TTL. It must not be inferred by the language model.

### Live capability packs

`agentbridge.payments.ProductionPaymentEngine` binds the deterministic selection to an allowlisted async connector registry. Daraja, Paystack, and MTN MoMo packs resolve secrets and tokenized recipients only at execution, reject floats, disable HTTP redirects, and return normalized non-terminal results for ambiguous transport outcomes. See the [provider connector audit](audits/provider-connectors-audit.md) before activation.

## 2. Safety and confirmation

MCP annotations improve client UX but are not authorization. AgentBridge independently enforces:

1. `payments:execute` OAuth scope;
2. a stable idempotency key;
3. payment/AML limits;
4. confirmation for **every** destructive tool;
5. enhanced review when the configured amount threshold is met;
6. provider circuit and timeout budgets.

`ConfirmationEvidence` stores only a verifier-issued reference, method, and subject. Raw OTPs and PINs must be verified by a PCI-separated identity service and must never enter prompts, traces, graph state, or OSCAL artifacts.

## 3. PostgreSQL checkpoints

Install the production extra:

```bash
pip install 'agentbridge-africa[postgres]'
export AGENTBRIDGE_POSTGRES_DSN='postgresql://...'
```

Run `postgres_saver(setup=True)` once as a controlled migration, then use `setup=False` in application replicas. Inject the yielded saver into `compile_payment_graph`; it refuses to compile without a saver and creates a checkpoint superstep after each lifecycle node.

Use `run_id` as LangGraph `thread_id`. `checkpoint_payload` serializes every `AgentState` field, including `PaymentLifecycle`, pending HITL, costs, retries, provider reference, traces, and partial artifact paths. Production databases should use encryption at rest and a restricted schema role; encrypted LangGraph serializers may be added where key management is available.

## 4. Trace-to-eval hydration

`evals.trace_hydrator` accepts OTLP JSON or Langfuse trace records and emits one typed `EvalCase` format. It:

- preserves source trace/observation links;
- hashes/redacts phone, recipient, account, token, OTP, PIN, and email values;
- scores required-parameter extraction;
- detects destructive calls without scope/idempotency;
- fails trajectories that exceed retry budgets;
- writes CI-friendly JSONL or uploads linked items using `create_dataset_item`.

Only sampled and scrubbed traces should leave the payment trust boundary. A human reviewer should supply corrected expected outputs for production failures before promoting them into the immutable golden set.

## 5. Typed cost hard-stops

`BudgetGuardian.charge` records `llm`, `processing`, and `other` costs in one USD ceiling. Currency conversion and gateway fee estimates must be fixed before charging. Crossing the ceiling sets `budget_exceeded`, returns HTTP 402, prevents another provider invocation, and atomically emits partial OSCAL Assessment Results and POA&M evidence.

The 402 response includes the LLM and processing-fee breakdown but never payment credentials or confirmation secrets.

## Deployment invariants

- No SQLite checkpoint backend in production.
- No destructive dispatch without server-validated confirmation evidence.
- No provider selected by model-generated arguments.
- No callback fulfils value without idempotent reconciliation and provider verification.
- No raw financial PII or authentication secret in traces/eval datasets.
- No graph starts in production without PostgreSQL persistence.
- No budget exception is downgraded to a warning.
