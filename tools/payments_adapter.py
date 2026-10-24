"""MCP-shaped payments adapter envelope."""
from __future__ import annotations

from typing import Any


def quote(amount: float, currency: str, rail: str = "preferred") -> dict[str, Any]:
    return {
        "ok": True,
        "data": {"quote_id": "q_001", "amount": amount, "currency": currency, "rail": rail},
        "error_code": None,
        "latency_ms": 42,
        "content_hash": "abc123",
        "read_only_hint": True,
    }


def execute(quote_id: str, idempotency_key: str) -> dict[str, Any]:
    return {
        "ok": True,
        "data": {"tx_id": "tx_001", "quote_id": quote_id, "status": "settled"},
        "error_code": None,
        "latency_ms": 120,
        "content_hash": "def456",
        "destructive_hint": True,
    }
