# AgentBridge Africa — DEVELOPMENT.md

> Multi-agent bridge: LangGraph-shaped pipeline + native MCP tools + ContextProfile
> + HTTP 402 budgets + NIST OSCAL + golden / production-trace evals.
> Local-context first (locale, rails, connectivity) — not a generic chatbot.

## Principles

1. Checkpoint after every tool call. Resume is a feature. `AgentState` is a
   **versioned public API** — new fields optional with defaults.
2. Budget is a hard stop (`budget_exceeded` + HTTP 402), not a soft warning.
   Partial OSCAL evidence is always committed.
3. `ContextProfile` lives in code paths, not only in prompts.
4. Evals before feature expansion. Golden trajectories in YAML; production
   traces sampled into the same harness.
5. Side-effect tools require idempotency keys, `payments:execute` scope, and
   HITL above threshold.
6. Tools act; resources read. Annotate both.

## Layout

```
.well-known/mcp.json
agentbridge/core/          orchestrator, graph, policy, router, budget, auth, resilience, state
agentbridge/tools/         MCP contracts + sandbox adapter + read-only resources
agentbridge/compliance/    oscal_exporter + NIST JSON v1.2.1 subset schemas
src/bridge/, tools/        deprecated compatibility imports (no canonical business logic)
profiles/                  en-NG.json, en-KE.json, offline-NG.json
evals/                     trajectories + harness + production_sampler
tests/                     test_budget_guardian.py + MCP/OAuth/OSCAL
```

## Definition of done

- [x] `evals/harness.py` exits 0
- [x] Native MCP annotations on every payment primitive
- [x] `.well-known/mcp.json` server card
- [x] HTTP 402 budget guardian + partial OSCAL
- [x] Automated POA&M on failed budget / AML / payment-limit controls
- [x] OAuth 2.1 PKCE + least-privilege scopes
- [x] HITL intercept for destructive tools over threshold
- [x] Circuit breakers + timeout budgets + fallback queue
- [x] OTEL-shaped traces
- [x] Production trace sampler
- [x] AgentState schema_version = 2 with optional new fields

## References (patterns borrowed)

- MCP tool annotations / server cards
- NIST OSCAL 1.2.1 assessment-results + plan-of-action-and-milestones
- Africa Payments MCP / civic-agent-kit (local rails as MCP tools)
- langgraph-agent-stack (per-run USD caps, golden eval YAML)
- comply54 (allow/block/escalate policy node)
- africa-deep-tech-agent / stacksng (offline / intermittent stress)
