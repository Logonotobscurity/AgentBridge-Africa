# AgentBridge Africa

<p align="center">
  <img src="docs/assets/social-preview.svg" alt="AgentBridge Africa: bounded multi-agent execution with hard budgets and regional context" width="100%" />
</p>

[![Python](https://img.shields.io/badge/Python-3.11+-blue)]()
[![Status](https://img.shields.io/badge/Status-hard%20budgets%20%2B%20evals-brightgreen)]()

**Multi-agent bridge with ContextProfile, hard budget stops, and MCP-shaped African payment adapters.**

Golden trajectory evals, offline failure modes, and regional profiles (en-NG, en-KE) so agent systems stay predictable in African operating environments.

---

## What problem does this solve?

Agent workflows lack hard cost ceilings, local context, and reliable regional tool adapters. Soft failures turn into infinite retries. AgentBridge enforces budget as a hard stop and evaluates against golden trajectories before expansion.

## What is implemented

- ContextProfile (locale, currency, rails, cost caps)
- BudgetGuardian hard stop
- MCP-shaped tool envelopes with safety hints
- Golden YAML trajectory harness (including failure injection)
- Profiles: en-NG, en-KE, offline-NG
- Payments adapter (quote / execute / status)

## Architecture / stack

Python 3.11+ · Pydantic v2 · PyYAML · pytest  
Eval harness runs fully offline.

## What a reviewer can run

```bash
pip install -r requirements.txt
make eval
```

## Portfolio value

Evidence of production-minded agent design: hard budgets, regional context, and deterministic evals instead of demo-only loops. Companion to ArchAudit CCM.

## Limitations

Planner is currently deterministic. Full multi-rail live adapters are stubbed for eval safety.

## Companion repositories

- [ArchAudit-CCM](https://github.com/Logonotobscurity/ArchAudit-CCM)
- [GigPilot](https://github.com/Logonotobscurity/GigPilot)
