# NIST OSCAL 1.2.1 continuous compliance

AgentBridge writes native OSCAL JSON after every run.

## Artifacts

```
.venturalitica/runs/{run_id}/
  assessment-results.oscal.json
  poam.oscal.json                  # only if any control is not-satisfied
```

Documents validate against `agentbridge/compliance/schemas/` (OSCAL 1.2.1 subset: uuid, metadata, findings, poam-items). Install `jsonschema` for full Draft-2020-12 validation; a required-key walker is used otherwise.

## Controls

| ID | Trigger | POA&M task |
|----|---------|------------|
| `AB-BUDGET-1` | `spent_usd > max_run_cost_usd` (HTTP 402) | Raise ceiling or reduce tool fan-out |
| `AB-AML-1` | amount vs `aml_daily_limit` / offline policy | Review AML rule or fail-closed path |
| `AB-RUN-1` | run did not reach `success` | Inspect `stop_reason` and traces |

## Partial evidence on 402

Budget exhaustion still **commits** the assessment pack so an interrupted graph is audit-ready. The 402 body lists `partial_artifacts`.
