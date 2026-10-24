# Architecture consolidation

## Commit-lineage analysis

The repository has one linear product history plus one squash-merged feature branch:

| Commit | Contribution | Design consequence |
|---|---|---|
| `2dcbb6e` | Initial repository and README | No runtime architecture yet. |
| `37042d2` | FDE Drive package: `src/bridge`, top-level `tools`, profiles, policy/budget pipeline, and seven eval trajectories | Established the executable prototype, but used two generic top-level namespaces. |
| `6116972` | Feature branch adding the `agentbridge` package, MCP discovery/safety, OAuth, HITL, resilience, telemetry, and OSCAL | Established the intended public package while retaining the prototype runtime. |
| `ed8a337` | Squash merge of `6116972` into `main` | Its tree is byte-for-byte identical to `6116972`; merging or cherry-picking that feature commit again would duplicate history, not code. |
| `98fe4dd` | Router budget/idempotency hardening, complete discovery schemas, and atomic/path-safe OSCAL persistence | Closed provider-dispatch and audit-integrity gaps in the merged design. |

`6116972^{tree}` and `ed8a337^{tree}` are both `acae22e50f4290c4b55f7cc7c2392c644c996d95`. The correct integration strategy is therefore **rework and consolidate on the current branch**, not merge the already-squashed feature branch.

## Consolidated design

`agentbridge` is now the only canonical implementation namespace:

```text
agentbridge/
├── core/
│   ├── orchestrator.py       # planner/worker/verifier run lifecycle
│   ├── graph.py              # optional LangGraph adapter
│   ├── policy.py             # allow/block/escalate decisions
│   ├── router.py             # A2A dispatch and final safety gates
│   ├── budget_guardian.py    # HTTP 402 and partial evidence
│   └── ...                   # state, OAuth, HITL, resilience, telemetry
├── tools/
│   ├── payment_mcp.py        # MCP contracts
│   ├── payment_adapter.py    # sandbox provider implementation
│   └── resources.py          # read-only data plane
└── compliance/
    └── oscal_exporter.py
```

The older `src.bridge.*` and `tools.payments_adapter` paths remain thin compatibility imports. They contain no business logic and can be removed in the next major version after downstream users migrate.

## Runtime boundaries

1. **Contracts:** `payment_mcp.py` and `.well-known/mcp.json` define schemas and risk hints.
2. **Policy and routing:** AML/payment limits, OAuth scope, connectivity, idempotency, HITL, circuit, timeout, and budget checks run before provider dispatch. `escalate` is a real pause with a HITL ticket, never an ignored advisory.
3. **Execution:** `payment_adapter.py` implements sandbox rail behavior. Fault injection is context-local so concurrent evaluations cannot contaminate one another.
4. **State:** one versioned `AgentState` moves through the lifecycle.
5. **Evidence:** terminal and HTTP 402 paths emit schema-validated OSCAL artifacts atomically.
6. **Compatibility:** legacy imports delegate inward to the canonical package; canonical code never imports legacy namespaces.

## Dependency rule

```text
compatibility shims → agentbridge orchestrator → core policy/router
                                      ├───────→ MCP contracts/adapters
                                      └───────→ OSCAL exporter
```

Imports in the opposite direction are prohibited. Evals and primary tests use `agentbridge.*`; compatibility paths are tested only to prevent accidental breakage.
