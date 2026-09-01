# AgentBridge Africa

Hardened multi-agent bridge for African payment rails: **native MCP safety annotations**, **`.well-known` server discovery**, **HTTP 402 budget hard-stops**, and **NIST OSCAL 1.2.1 audit packs**.

Local-context first (locale, rails, connectivity) — not a generic chatbot.

Inspired by: [africa-payments-mcp](https://github.com/kenyaclaw/africa-payments-mcp), [mpesa-mcp](https://github.com/gabrielmahia/mpesa-mcp), LangGraph budget control, Harbor/LangChain golden-trajectory evals, NIST OSCAL.

## Runtime shape

```
oauth/pkce → policy_gate → planner → worker → HITL → verifier → BudgetGuardian (402)
                 │                                              │
                 └─ MCP tools / resources                       └─ OSCAL AR + POA&M
```

| Piece | Role |
|-------|------|
| MCP Server Card | `.well-known/mcp.json` — indexable discovery, no client handshake required |
| Tool annotations | `readOnly` / `destructive` / `idempotent` (+ MCP `*Hint` aliases) |
| Resources | Read-only state, profiles, balances, OSCAL artifacts |
| `BudgetGuardian` | Hard stop + **HTTP 402** + partial OSCAL evidence |
| OAuth 2.1 PKCE | Remote MCP endpoints are not unauthenticated proxies |
| HITL gate | Every destructive tool requires verified OTP/PIN/OAuth confirmation |
| Circuit breaker | Per-provider open/half-open; fallback queue on outage |
| `AgentState` v2 | Versioned public API; new fields optional with defaults |
| OSCAL exporter | Assessment Results + POA&M under `.venturalitica/runs/{run_id}/` |
| Eval harness | Golden YAML trajectories + production-trace sampler |

## Layout

```
AgentBridge-Africa/
├── .well-known/mcp.json              # MCP Server Card
├── agentbridge/
│   ├── core/
│   │   ├── orchestrator.py           # planner / worker / verifier lifecycle
│   │   ├── graph.py                  # checkpointed payment lifecycle graph
│   │   ├── checkpointing.py          # PostgresSaver / AsyncPostgresSaver
│   │   ├── rail_switch.py            # currency/country/health provider router
│   │   ├── policy.py                 # allow / block / escalate
│   │   ├── budget_guardian.py        # typed HTTP 402 cost hard-stop
│   │   ├── router.py                 # A2A routing
│   │   ├── oauth.py                  # OAuth 2.1 + PKCE
│   │   ├── hitl.py                   # destructive-tool interceptors
│   │   ├── circuit_breaker.py
│   │   ├── telemetry.py              # OTEL-shaped traces
│   │   └── state.py                  # AgentState schema v2
│   ├── payments/                     # live-ready async capability packs
│   │   ├── engine.py                 # production ContextProfile facade
│   │   ├── daraja.py                 # Safaricom OAuth, STK, status query
│   │   ├── paystack.py               # initialize + verify transaction
│   │   ├── mtn_momo.py               # request-to-pay + status query
│   │   ├── runtime.py                # secrets, egress allowlist, transport
│   │   └── registry.py               # allowlisted dependency injection
│   ├── tools/
│   │   ├── payment_mcp.py            # unified annotated MCP contracts
│   │   ├── payment_engine.py         # provider-neutral payment facade
│   │   ├── payment_adapter.py        # sandbox provider implementation
│   │   └── resources.py              # read-only resources
│   ├── webhooks/
│   │   ├── security.py               # HMAC/token/SPIFFE verification
│   │   └── handlers.py               # dedupe + reconcile, never callback-final
│   └── compliance/
│       ├── oscal_exporter.py
│       └── schemas/                  # OSCAL JSON v1.2.1 subset
├── agentbridge/migrations/            # ledger + atomic callback/outbox SQL
├── src/bridge/                       # deprecated compatibility imports only
├── tools/                            # deprecated compatibility imports/stub
├── evals/                            # golden + production sampler
└── tests/test_budget_guardian.py
```

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
make eval
python -m pytest -q
# Live connector transport (credentials are still deployment-owned):
pip install '.[connectors]'
```

Discovery card (no live connection required):

```bash
cat .well-known/mcp.json
```

## MCP safety annotations

Tools **act**. Resources **read**. Orchestrators gate on flags:

```python
from agentbridge.tools import MPESA_STK_PUSH, MPESA_QUERY_STATUS

MPESA_STK_PUSH.annotations
# readOnly=False  destructive=True  idempotent=False

MPESA_QUERY_STATUS.annotations
# readOnly=True   destructive=False idempotent=True
```

Destructive tools require `idempotency_key`, `payments:execute` scope, and verifier-backed OTP/PIN/OAuth confirmation. Every destructive call pauses in `awaiting_hitl`; amounts above `hitl_amount_threshold` receive enhanced review.

## Budget → HTTP 402

When `spent_usd > max_run_cost_usd` the guardian:

1. Sets `status=budget_exceeded` and `http_status=402`
2. Halts further tool invocations
3. Writes partial OSCAL evidence to `.venturalitica/runs/{run_id}/`

## OSCAL continuous compliance

```python
from agentbridge.compliance import Finding, export_oscal_results

export_oscal_results(run_id, findings)
# → assessment-results.oscal.json
# → poam.oscal.json          (only when a control is not-satisfied)
```

Failed budget, AML, or payment-limit controls auto-generate a Plan of Action and Milestones linking each finding to a remediation task.

## Metrics (from `make eval`)

| Metric | Meaning |
|--------|---------|
| `n` | Trajectory count (happy + failure injection) |
| `success_rate_pct` | Golden trajectories matching expected outcome |
| `p50_latency_ms` | Median summed step latency |
| `median_cost_usd` | Median spend under BudgetGuardian |

Latest sandbox run: **7 / 7 expected outcomes (100%)**.

## Profiles

| File | Locale | Currency | Rails | Connectivity |
|------|--------|----------|-------|--------------|
| `profiles/en-NG.json` | en-NG | NGN | bank, ussd, mobile_money, paystack | intermittent |
| `profiles/en-KE.json` | en-KE | KES | mpesa, mobile_money, bank | intermittent |
| `profiles/offline-NG.json` | en-NG | NGN | ussd | **offline_first** (fail closed) |

## Safety

- `execute` requires `idempotency_key`
- Tool envelopes expose `readOnly` / `destructive` / `idempotent`
- Budget is a **hard stop** (HTTP 402), not a soft warning
- `offline_first` blocks remote and side-effect tools before the worker runs
- No infinite retry on tool timeout; circuit breakers open after repeated provider faults
- OAuth 2.1 + PKCE on remote MCP; tokens bound to exact scopes
- OpenTelemetry-shaped traces on every LLM / tool / policy span

See [`DEVELOPMENT.md`](DEVELOPMENT.md), [`docs/architecture.md`](docs/architecture.md), [`docs/production-architecture.md`](docs/production-architecture.md), [`docs/webhooks.md`](docs/webhooks.md), the [`Context Router & PostgreSQL FSM audit`](docs/audits/context-router-postgres-fsm-audit.md), the [`Provider Connector audit`](docs/audits/provider-connectors-audit.md), [`docs/playbook.md`](docs/playbook.md), [`docs/mcp-safety.md`](docs/mcp-safety.md), and [`docs/oscal.md`](docs/oscal.md).

Default execution remains sandboxed. Live connector classes perform no network I/O until explicitly registered with deployment-owned secrets, policy, callback hosts, and egress configuration.
