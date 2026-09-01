"""MCP Tool / Resource types with safety annotations.

Matches the MCP tool annotation hints (``readOnlyHint`` / ``destructiveHint`` /
``idempotentHint``) and the orchestrator-facing aliases requested by
AgentBridge (``readOnly`` / ``destructive`` / ``idempotent``).

If the official ``mcp`` package is installed, ``Tool`` is that type; otherwise
a compatible stand-in is used so the runtime stays sandbox-installable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

try:  # optional
    from mcp.types import Tool as _McpTool  # type: ignore
except Exception:  # pragma: no cover
    _McpTool = None


def normalize_annotations(raw: dict[str, Any] | None) -> dict[str, Any]:
    raw = dict(raw or {})
    read_only = bool(raw.get("readOnly", raw.get("readOnlyHint", False)))
    destructive = bool(raw.get("destructive", raw.get("destructiveHint", False)))
    idempotent = bool(raw.get("idempotent", raw.get("idempotentHint", False)))
    open_world = bool(raw.get("openWorldHint", True))
    return {
        "readOnly": read_only,
        "destructive": destructive,
        "idempotent": idempotent,
        "readOnlyHint": read_only,
        "destructiveHint": destructive,
        "idempotentHint": idempotent,
        "openWorldHint": open_world,
    }


@dataclass
class LocalTool:
    name: str
    description: str
    inputSchema: dict[str, Any]
    annotations: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.annotations = normalize_annotations(self.annotations)


def Tool(  # noqa: N802 — MCP constructor shape
    *,
    name: str,
    description: str,
    inputSchema: dict[str, Any],
    annotations: dict[str, Any] | None = None,
) -> Any:
    anns = normalize_annotations(annotations)
    if _McpTool is not None:
        try:
            return _McpTool(
                name=name,
                description=description,
                inputSchema=inputSchema,
                annotations=anns,
            )
        except Exception:
            pass
    return LocalTool(name=name, description=description, inputSchema=inputSchema, annotations=anns)


def annotations_dict(tool: Any) -> dict[str, Any]:
    raw = getattr(tool, "annotations", None)
    if raw is None:
        return normalize_annotations({})
    if hasattr(raw, "model_dump"):
        raw = raw.model_dump()
    elif hasattr(raw, "__dict__") and not isinstance(raw, dict):
        raw = {k: v for k, v in vars(raw).items() if not k.startswith("_")}
    return normalize_annotations(raw)


@dataclass
class Resource:
    uri: str
    name: str
    description: str
    mimeType: str = "application/json"
    annotations: dict[str, Any] = field(default_factory=lambda: normalize_annotations({"readOnly": True, "idempotent": True}))
