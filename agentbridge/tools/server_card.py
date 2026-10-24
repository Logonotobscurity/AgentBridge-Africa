"""Build the static MCP discovery card from canonical runtime contracts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agentbridge.tools.mcp_types import annotations_dict
from agentbridge.tools.payment_mcp import PAYMENT_TOOLS
from agentbridge.tools.resources import PAYMENT_RESOURCES


def synchronize_server_card(path: Path) -> dict[str, Any]:
    card = json.loads(path.read_text(encoding="utf-8"))
    card["tools"] = [
        {
            "name": tool.name,
            "description": tool.description,
            "annotations": annotations_dict(tool),
            "inputSchema": tool.inputSchema,
        }
        for tool in PAYMENT_TOOLS
    ]
    card["resources"] = [
        {
            "uri": resource.uri,
            "name": resource.name,
            "description": resource.description,
            "mimeType": resource.mimeType,
            "annotations": annotations_dict(resource),
        }
        for resource in PAYMENT_RESOURCES
    ]
    path.write_text(json.dumps(card, indent=2) + "\n", encoding="utf-8")
    return card
