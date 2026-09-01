# AgentBridge Africa — DEVELOPMENT.md

> Multi-agent bridge: LangGraph-shaped pipeline + MCP tools + ContextProfile + budgets + trajectory evals.
> Metrics: **trajectory success rate**, **p50 latency**, **median cost per success**.
> Local-context first (locale, rails, connectivity) — not a generic chatbot.

## Principles

1. Checkpoint after every tool call. Resume is a feature.
2. Budget is a hard stop (`budget_exceeded`), not a soft warning.
3. `ContextProfile` lives in code paths, not only in prompts.
4. Evals before feature expansion. Golden trajectories in YAML.
5. Side-effect tools require idempotency keys; prefer real African MCP servers when available.

## Layout

```
agentbridge/
  src/bridge/
    graph.py
    nodes.py          # planner, worker, verifier
    state.py
    budget.py         # BudgetGuardian
    policy_gate.py    # allow | block | escalate
  profiles/           # en-NG.json, en-KE.json, offline-NG.json
  tools/              # MCP-shaped adapter + stub
  evals/
    trajectories/*.yaml
    harness.py
    results/latest.json
  tests/
  docs/playbook.md
  Makefile
```

## ContextProfile (minimum)

```json
{
  "locale": "en-NG",
  "currency": "NGN",
  "id_formats": ["NIN", "BVN"],
  "payment_rails": ["bank", "ussd", "mobile_money"],
  "connectivity": "intermittent",
  "max_tool_latency_ms": 8000,
  "max_run_cost_usd": 0.15,
  "language_preference": "en"
}
```

## Trajectory fixture (minimum)

```yaml
id: pay_quote_ngn_001
goal: "Quote NGN transfer via preferred rail"
profile: profiles/en-NG.json
expected:
  success: true
  schema: QuoteResult
inject_fault: null   # or tool_timeout | verifier_reject | provider_error | budget
```

## Definition of done

- [x] `evals/harness.py` exits 0
- [x] README table: N, success rate, p50 latency, median cost_usd
- [x] `docs/playbook.md`: resume, budget, add-a-tool
- [x] No infinite retry on tool timeout (max one retry then fail step)
- [x] ≥6 trajectories including failure modes
- [x] Budget hard stop demonstrated
- [x] Offline fails closed
- [x] Multi-rail adapter envelope with safety hints

## References (patterns borrowed)

- Africa Payments MCP / civic-agent-kit (local rails as MCP tools)
- langgraph-agent-stack (per-run USD caps, golden eval YAML)
- comply54 (allow/block/escalate policy node)
- africa-deep-tech-agent / stacksng (offline / intermittent stress)
