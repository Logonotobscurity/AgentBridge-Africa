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
| HITL gate | Destructive tools above amount threshold pause for an operator |
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
│   │   ├── budget_guardian.py        # HTTP 402 hard-stop
│   │   ├── router.py                 # A2A routing
│   │   ├── oauth.py                  # OAuth 2.1 + PKCE
│   │   ├── hitl.py                   # destructive-tool interceptors
│   │   ├── circuit_breaker.py
│   │   ├── telemetry.py              # OTEL-shaped traces
│   │   └── state.py                  # AgentState schema v2
│   ├── tools/
│   │   ├── payment_mcp.py            # annotated payment tools
│   │   └── resources.py              # read-only resources
│   └── compliance/
│       ├── oscal_exporter.py
│       └── schemas/                  # OSCAL JSON v1.2.1 subset
├── src/bridge/                       # existing planner/worker/verifier
├── evals/                            # golden + production sampler
└── tests/test_budget_guardian.py
```

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
make eval
python -m pytest -q
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

Destructive tools require `idempotency_key` and `payments:execute` scope. Amounts above `hitl_amount_threshold` pause in `awaiting_hitl`.

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

See [`DEVELOPMENT.md`](DEVELOPMENT.md), [`docs/playbook.md`](docs/playbook.md), [`docs/mcp-safety.md`](docs/mcp-safety.md), and [`docs/oscal.md`](docs/oscal.md).

Sandbox only — synthetic quotes, no live payment rails, no real PII.
