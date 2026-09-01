# AgentBridge Africa

Multi-agent bridge with **ContextProfile**, **hard budgets**, **MCP-shaped payment adapters**, and **golden trajectory evals**.

Local-context first (locale, rails, connectivity) — not a generic chatbot.

Inspired by: [africa-payments-mcp](https://github.com/kenyaclaw/africa-payments-mcp), [mpesa-mcp](https://github.com/gabrielmahia/mpesa-mcp), LangGraph budget control (`runcycles`), Harbor/LangChain golden-trajectory evals.

## Runtime shape

```
policy_gate → planner → worker → verifier → BudgetGuardian
                 │
                 └─ MCP-shaped tools: quote / execute / status
```

| Piece | Role |
|-------|------|
| `ContextProfile` | locale, currency, rails, connectivity, cost/latency caps — **in code paths**, not only prompts |
| Planner | Deterministic goal → step plan |
| Worker | MCP-shaped tool call |
| Verifier | Schema / business rule on step output |
| `BudgetGuardian` | Hard stop when `spent_usd > max_run_cost_usd` |
| PolicyGate | `allow` \| `block` \| `escalate` |
| Eval harness | Golden YAML trajectories → success rate, p50 latency, median cost |

## Metrics (from `make eval`)

| Metric | Meaning |
|--------|---------|
| `n` | Trajectory count (happy + failure injection) |
| `success_rate_pct` | Golden trajectories matching expected outcome |
| `p50_latency_ms` | Median summed step latency |
| `median_cost_usd` | Median spend under BudgetGuardian |
| `median_cost_per_success_usd` | Median spend on `status=success` runs |

Latest sandbox run: **7 / 7 expected outcomes (100%)**.

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
make eval
cat evals/results/latest.json
python -m pytest -q
```

## Profiles

| File | Locale | Currency | Rails | Connectivity |
|------|--------|----------|-------|--------------|
| `profiles/en-NG.json` | en-NG | NGN | bank, ussd, mobile_money, paystack | intermittent |
| `profiles/en-KE.json` | en-KE | KES | mpesa, mobile_money, bank | intermittent |
| `profiles/offline-NG.json` | en-NG | NGN | ussd | **offline_first** (fail closed) |

## Trajectories

| ID | Expected |
|----|----------|
| `pay_quote_ngn_001` | success |
| `pay_quote_kes_001` | success |
| `pay_timeout_001` | fail (`tool_timeout`) |
| `pay_provider_err_001` | fail (`provider_unavailable`) |
| `pay_verifier_001` | fail (`verifier_reject`) |
| `pay_offline_001` | fail closed |
| `pay_budget_001` | `budget_exceeded` |

## Safety

- `execute` requires `idempotency_key`
- Tool envelopes expose `read_only_hint` / `destructive_hint`
- Budget is a **hard stop**, not a soft warning
- `offline_first` blocks remote and side-effect tools before the worker runs
- No infinite retry on tool timeout (fail the step)

See [`DEVELOPMENT.md`](DEVELOPMENT.md) and [`docs/playbook.md`](docs/playbook.md).

## Layout

```
src/bridge/     state, budget, nodes, policy_gate, graph
tools/          payments_adapter (quote/execute/status) + legacy stub
profiles/       en-NG, en-KE, offline-NG
evals/          trajectories + harness → results/latest.json
tests/
docs/playbook.md
```

Sandbox only — synthetic quotes, no live payment rails, no real PII.
