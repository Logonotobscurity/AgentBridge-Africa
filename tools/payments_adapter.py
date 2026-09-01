"""Payment adapter — MCP-shaped, multi-rail (inspired by africa-payments-mcp / mpesa-mcp).

Sandbox only: no real network. Tools annotated for agent safety:
  - quote / status → read-ish
  - execute → side-effect (requires idempotency_key)
"""
from __future__ import annotations

import time
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

Rail = Literal["bank", "ussd", "mobile_money", "mpesa", "paystack", "mtn_momo"]

# Simulated provider latency / failure hooks for eval injection
_FORCE_TIMEOUT = False
_FORCE_ERROR: str | None = None


def set_fault(timeout: bool = False, error: str | None = None) -> None:
    global _FORCE_TIMEOUT, _FORCE_ERROR
    _FORCE_TIMEOUT = timeout
    _FORCE_ERROR = error


def clear_fault() -> None:
    set_fault(False, None)


class ToolEnvelope(BaseModel):
    ok: bool
    data: Any = None
    error_code: str | None = None
    latency_ms: int = 0
    content_hash: str = ""
    cost_estimate: float = 0.0
    # MCP-style safety hints (mpesa-mcp / financial tool pattern)
    read_only_hint: bool = True
    destructive_hint: bool = False


def _hash(payload: Any) -> str:
    import hashlib
    import json

    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def quote(
    rail: Rail,
    amount: float,
    currency: str,
    *,
    country: str = "NG",
) -> ToolEnvelope:
    t0 = time.perf_counter()
    if _FORCE_TIMEOUT:
        time.sleep(0.05)
        return ToolEnvelope(
            ok=False,
            error_code="tool_timeout",
            latency_ms=int((time.perf_counter() - t0) * 1000),
            read_only_hint=True,
        )
    if _FORCE_ERROR:
        return ToolEnvelope(
            ok=False,
            error_code=_FORCE_ERROR,
            latency_ms=1,
            read_only_hint=True,
        )
    if amount <= 0:
        return ToolEnvelope(ok=False, error_code="invalid_amount", latency_ms=1, read_only_hint=True)
    if currency not in ("NGN", "KES", "GHS", "UGX", "USD"):
        return ToolEnvelope(ok=False, error_code="unsupported_currency", latency_ms=1, read_only_hint=True)

    fee_rate = 0.015 if rail in ("mobile_money", "mpesa", "mtn_momo") else 0.01
    data = {
        "quote_id": uuid4().hex[:12],
        "rail": rail,
        "amount": amount,
        "currency": currency,
        "country": country,
        "fee": round(amount * fee_rate, 2),
        "provider": {
            "mpesa": "safaricom",
            "paystack": "paystack",
            "mtn_momo": "mtn",
            "mobile_money": "generic_mm",
            "bank": "local_bank",
            "ussd": "ussd_gateway",
        }.get(rail, "generic"),
    }
    return ToolEnvelope(
        ok=True,
        data=data,
        latency_ms=int((time.perf_counter() - t0) * 1000),
        content_hash=_hash(data),
        cost_estimate=0.001,
        read_only_hint=True,
        destructive_hint=False,
    )


def execute(
    rail: Rail,
    quote_id: str,
    idempotency_key: str,
) -> ToolEnvelope:
    t0 = time.perf_counter()
    if not idempotency_key:
        return ToolEnvelope(
            ok=False,
            error_code="missing_idempotency_key",
            latency_ms=1,
            read_only_hint=False,
            destructive_hint=True,
        )
    if _FORCE_ERROR:
        return ToolEnvelope(
            ok=False,
            error_code=_FORCE_ERROR,
            latency_ms=1,
            read_only_hint=False,
            destructive_hint=True,
        )
    data = {
        "txn_id": uuid4().hex[:16],
        "quote_id": quote_id,
        "rail": rail,
        "status": "accepted",
        "idempotency_key": idempotency_key,
    }
    return ToolEnvelope(
        ok=True,
        data=data,
        latency_ms=int((time.perf_counter() - t0) * 1000),
        content_hash=_hash(data),
        cost_estimate=0.002,
        read_only_hint=False,
        destructive_hint=True,
    )


def status(txn_id: str) -> ToolEnvelope:
    t0 = time.perf_counter()
    data = {"txn_id": txn_id, "status": "settled"}
    return ToolEnvelope(
        ok=True,
        data=data,
        latency_ms=int((time.perf_counter() - t0) * 1000),
        content_hash=_hash(data),
        cost_estimate=0.0005,
        read_only_hint=True,
        destructive_hint=False,
    )
