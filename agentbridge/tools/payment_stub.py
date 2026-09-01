"""Local stub mimicking africa-payments-mcp quote/execute shape."""
from __future__ import annotations

import time
from typing import Any
from uuid import uuid4


def quote(rail: str, amount: float, currency: str) -> dict[str, Any]:
    t0 = time.perf_counter()
    if amount <= 0:
        return {"ok": False, "error_code": "invalid_amount", "latency_ms": 1}
    qid = uuid4().hex[:12]
    data = {
        "quote_id": qid,
        "rail": rail,
        "amount": amount,
        "currency": currency,
        "fee": round(amount * 0.01, 2),
    }
    return {
        "ok": True,
        "data": data,
        "error_code": None,
        "latency_ms": int((time.perf_counter() - t0) * 1000),
        "cost_estimate": 0.001,
    }


def execute(rail: str, quote_id: str, idempotency_key: str) -> dict[str, Any]:
    t0 = time.perf_counter()
    if not idempotency_key:
        return {"ok": False, "error_code": "missing_idempotency_key", "latency_ms": 1}
    return {
        "ok": True,
        "data": {"txn_id": uuid4().hex[:16], "quote_id": quote_id, "rail": rail, "status": "accepted"},
        "error_code": None,
        "latency_ms": int((time.perf_counter() - t0) * 1000),
        "cost_estimate": 0.002,
    }
