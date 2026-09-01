# AgentBridge playbook

Local-context first. Not a generic chatbot. This page is the stranger-run
guide: resume, budget, add-a-tool, MCP, OSCAL.

## Resume

Every successful quote writes `state.checkpoint = { last_quote_id }`.

1. Re-load the same `ContextProfile` JSON.
2. Pass `execute_payment=True` (or call `tools.payments_adapter.execute` with
   the checkpoint `quote_id` **and** a fresh `idempotency_key`).
3. Do not replay `quote` unless the checkpoint is missing — quotes are cheap
   but not free under `BudgetGuardian`.

`AgentState.schema_version` is **2**. New fields (`http_status`, `hitl_pending`,
`traces`, `fallback_queue`, `oauth_scopes`) are optional with defaults, so a
v1 checkpoint still deserializes on a v2 worker.

## Budget (HTTP 402)

`BudgetGuardian` (`agentbridge/core/budget_guardian.py`) is a **hard stop**:

- `spent_usd` is incremented **before** the next side-effect.
- If `spent_usd > profile.max_run_cost_usd` → `status = budget_exceeded`,
  `http_status = 402`, `stop_reason` set, **no further spend**.
- Intermediate OSCAL evidence is committed under `.venturalitica/runs/{run_id}/`.
- Soft warnings are forbidden. Infinite retry is forbidden.

Force it: `run_budget_exhaustion()` or trajectory `pay_budget_001`.

## HITL

Destructive tools (`destructive: true`) whose `amount` exceeds
`profile.hitl_amount_threshold` pause in `awaiting_hitl` until an operator
calls `HitlGate.decide(ticket_id, "approved")`.

## Circuit breakers

Each provider (`mpesa`, `paystack`, `bank`) is wrapped in a breaker. After
`failure_threshold` faults the circuit opens; the router enqueues the tool on
`state.fallback_queue` instead of retry-storming.

## Add a tool

1. Declare it in `agentbridge/tools/payment_mcp.py` with **explicit**
   `readOnly` / `destructive` / `idempotent` annotations.
2. If it is read-only state, add a **Resource** in `agentbridge/tools/resources.py`
   instead of a Tool.
3. Register the name in `src/bridge/policy_gate.py` (`allow|block|escalate`).
4. Teach the planner in `src/bridge/nodes.py` (`plan()`).
5. Expose it on `.well-known/mcp.json`.
6. Add a golden YAML under `evals/trajectories/` (happy + at least one fault).
7. `make eval` must stay at 100% expected-outcome match.

## Production traces → evals

```bash
PYTHONPATH=. python -c "from evals.production_sampler import score_file; \
  from pathlib import Path; print(score_file(Path('evals/results/latest.json')))"
```

Live spans become YAML cases scored on **tool-argument accuracy** and
**execution grounding** (idempotency + scope).

## Commands

```bash
pip install -r requirements.txt
make eval          # harness → evals/results/latest.json (exit 0 at 100%)
PYTHONPATH=. python -m pytest -q
```
