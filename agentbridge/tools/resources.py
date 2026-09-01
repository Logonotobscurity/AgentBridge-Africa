"""Read-only MCP resources — state, profiles, and compliance artifacts.

Resources never move money. They are annotated readOnly + idempotent so
orchestrators can fetch them without HITL or execute scopes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agentbridge.tools.mcp_types import Resource, normalize_annotations

_READ = normalize_annotations({"readOnly": True, "destructive": False, "idempotent": True})

RUN_STATE = Resource(
    uri="agentbridge://runs/{run_id}/state",
    name="run_state",
    description="Versioned AgentState checkpoint for an in-flight or completed run",
    annotations=_READ,
)

PROFILE_RESOURCE = Resource(
    uri="agentbridge://profiles/{locale}",
    name="context_profile",
    description="Locale-bound ContextProfile (currency, rails, connectivity, caps)",
    annotations=_READ,
)

WALLET_BALANCE = Resource(
    uri="agentbridge://wallet/{account}/balance",
    name="wallet_balance",
    description="Read-only wallet / ledger balance. Never a transfer primitive.",
    annotations=_READ,
)

ASSESSMENT_RESULTS = Resource(
    uri="agentbridge://compliance/{run_id}/assessment-results",
    name="oscal_assessment_results",
    description="NIST OSCAL Assessment Results for a run",
    annotations=_READ,
)

POAM_RESOURCE = Resource(
    uri="agentbridge://compliance/{run_id}/poam",
    name="oscal_poam",
    description="NIST OSCAL Plan of Action and Milestones generated from failed controls",
    annotations=_READ,
)

MCP_SERVER_CARD = Resource(
    uri="agentbridge://.well-known/mcp.json",
    name="mcp_server_card",
    description="MCP server discovery card (tools, resources, auth, scopes)",
    annotations=_READ,
)

PAYMENT_RESOURCES = [
    RUN_STATE,
    PROFILE_RESOURCE,
    WALLET_BALANCE,
    ASSESSMENT_RESULTS,
    POAM_RESOURCE,
    MCP_SERVER_CARD,
]


def read_resource(uri: str, *, root: Path | None = None) -> dict[str, Any]:
    """Resolve a resource URI to JSON. Read-only; never executes a tool."""
    root = root or Path.cwd()
    if uri.endswith("mcp.json") or uri.endswith(".well-known/mcp.json"):
        path = root / ".well-known" / "mcp.json"
        return json.loads(path.read_text())
    if "/profiles/" in uri:
        locale = uri.rstrip("/").split("/")[-1]
        path = root / "profiles" / f"{locale}.json"
        return json.loads(path.read_text())
    if "/assessment-results" in uri:
        run_id = uri.split("/compliance/")[-1].split("/")[0]
        path = root / ".venturalitica" / "runs" / run_id / "assessment-results.oscal.json"
        return json.loads(path.read_text())
    if uri.endswith("/poam"):
        run_id = uri.split("/compliance/")[-1].split("/")[0]
        path = root / ".venturalitica" / "runs" / run_id / "poam.oscal.json"
        return json.loads(path.read_text())
    if "/runs/" in uri and uri.endswith("/state"):
        raise FileNotFoundError("run state is in-memory unless a checkpoint was persisted")
    raise ValueError(f"unsupported resource uri: {uri}")
