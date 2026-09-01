# AgentBridge playbook

Local-context first. Not a generic chatbot. This page is the stranger-run
guide: resume, budget, add-a-tool.

## Resume

Every successful quote writes `state.checkpoint = { last_quote_id }`.

1. Re-load the same `ContextProfile` JSON.
2. Pass `execute_payment=True` (or call `tools.payments_adapter.execute` with
   the checkpoint `quote_id` **and** a fresh `idempotency_key`).
3. Do not replay `quote` unless the checkpoint is missing — quotes are cheap
   but not free under `BudgetGuardian`.

Checkpoints are in-process today (dict on `BridgeState`). Persist the JSON
blob if you need crash-resume across processes.

## Budget

`BudgetGuardian` (`src/bridge/budget.py`) is a **hard stop**:

- `spent_usd` is incremented **before** the next side-effect.
- If `spent_usd > profile.max_run_cost_usd` → `status = budget_exceeded`,
  `stop_reason` set, **no further spend**.
- Soft warnings are forbidden. Infinite retry is forbidden (max one retry
  then fail the step — v1 fails the step immediately on tool error).

Force it: `run_budget_exhaustion()` or trajectory `pay_budget_001`.

## Add a tool

1. Implement an MCP-shaped envelope in `tools/`:

   ```
   { ok, data, error_code, latency_ms, content_hash,
     cost_estimate, read_only_hint, destructive_hint }
   ```

2. Side-effect tools **must** require `idempotency_key` and set
   `destructive_hint=True`.
3. Register the name in `src/bridge/policy_gate.py` (`allow|block|escalate`).
4. Teach the planner in `src/bridge/nodes.py` (`plan()`).
5. Add a golden YAML under `evals/trajectories/` (happy + at least one fault).
6. `make eval` must stay at 100% expected-outcome match.

Prefer real African MCP servers (africa-payments-mcp, mpesa-mcp) behind the
same envelope when leaving the sandbox.

## Failure injection

Trajectory field `inject_fault`:

| Value | Behavior |
|-------|----------|
| `null` | Happy path |
| `tool_timeout` | Adapter returns `tool_timeout` |
| `provider_error` | Adapter returns `provider_unavailable` |
| `verifier_reject` | Verifier fails even if quote looks ok |
| `budget` | Cap lowered until `budget_exceeded` |

Offline profiles (`connectivity: offline_first`) fail closed **before** any
tool call.

## Commands

```bash
pip install -r requirements.txt
make eval          # harness → evals/results/latest.json (exit 0 at 100%)
python -m pytest -q
```
