# AgentBridge Africa

<p align="center">
  <strong>Multi-agent bridge with hard budgets and regional context</strong><br/>
  ContextProfile · BudgetGuardian · Golden trajectory evals
</p>

[![Python](https://img.shields.io/badge/Python-3.11+-blue?style=flat)]()
[![Status](https://img.shields.io/badge/Status-hard%20budgets%20%2B%20evals-brightgreen?style=flat)]()
[![License](https://img.shields.io/badge/License-MIT-yellow?style=flat)]()

---

## What problem does this solve?

Agent workflows lack hard cost ceilings, local context, and reliable regional tool adapters. Soft failures turn into infinite retries. AgentBridge enforces budget as a hard stop and evaluates against golden trajectories before expansion.

## Architecture

```mermaid
flowchart LR
  A[ContextProfile<br/>locale / currency / rails] --> B[Planner]
  B --> C[Worker<br/>MCP tools]
  C --> D[BudgetGuardian<br/>hard stop]
  D --> E[Verifier]
  E --> F[Golden evals]
```

**Stack:** Python 3.11+ · Pydantic v2 · PyYAML · pytest  
Eval harness runs fully offline.

## What is implemented

- ContextProfile (locale, currency, rails, cost caps)
- BudgetGuardian hard stop
- MCP-shaped tool envelopes with safety hints
- Golden YAML trajectory harness (including failure injection)
- Profiles: en-NG, en-KE, offline-NG
- Payments adapter (quote / execute / status)

## Quick start

```bash
pip install -r requirements.txt
make eval
```

## Demo

<!-- 
CLI demo recommended (asciinema + agg):
  asciinema rec demo.cast && make eval && agg demo.cast demo.gif
Web demo later:
  npx screencli record <url> -p "Run eval harness and show budget hard stop"
-->

> Demo GIF coming soon. Run `make eval` to see the golden trajectory results.

## Portfolio value

Evidence of production-minded agent design: hard budgets, regional context, and deterministic evals instead of demo-only loops. Companion to ArchAudit CCM.

## Limitations

Planner is currently deterministic. Full multi-rail live adapters are stubbed for eval safety.

## Companion repositories

- [ArchAudit-CCM](https://github.com/Logonotobscurity/ArchAudit-CCM)
- [GigPilot](https://github.com/Logonotobscurity/GigPilot)

## Visual assets checklist

- [ ] Social preview image (1280×640)
- [ ] Demo GIF of eval harness (asciinema recommended)
- [ ] Optional logo in `docs/assets/`
