"""Deterministic provider rail selection behind the unified MCP contract.

Provider choice is policy, not an LLM decision. The router combines transaction
facts, the locale-bound ContextProfile, configured rails, and live health.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from agentbridge.core.state import ContextProfile


class RailUnavailableError(RuntimeError):
    """No configured and healthy provider can serve a payment intent."""


@dataclass(frozen=True)
class RailSelection:
    provider: str
    rail: str
    currency: str
    destination_country: str
    rationale: str


# Ordered failover preferences. Bank/USSD remain useful local fallbacks while
# the three primary gateways cover the unified pan-African interface.
_DEFAULT_ROUTES: dict[tuple[str, str], tuple[str, ...]] = {
    ("KES", "KE"): ("mpesa", "mobile_money", "bank"),
    ("NGN", "NG"): ("paystack", "bank", "ussd", "mobile_money"),
    ("GHS", "GH"): ("mtn_momo", "paystack", "mobile_money", "bank"),
    ("UGX", "UG"): ("mtn_momo", "mobile_money", "bank"),
    ("ZAR", "ZA"): ("paystack", "bank"),
    ("USD", "NG"): ("paystack", "bank"),
    ("USD", "KE"): ("paystack", "bank"),
}

_PROVIDER_BY_RAIL = {
    "mpesa": "safaricom",
    "paystack": "paystack",
    "mtn_momo": "mtn",
    "mobile_money": "mobile_money",
    "bank": "bank",
    "ussd": "ussd_gateway",
}


@dataclass
class RailRouter:
    routes: Mapping[tuple[str, str], tuple[str, ...]] = field(default_factory=lambda: _DEFAULT_ROUTES)

    def select(
        self,
        profile: ContextProfile,
        *,
        currency: str,
        destination_country: str | None = None,
        availability: Mapping[str, bool] | None = None,
    ) -> RailSelection:
        currency = currency.upper()
        country = (destination_country or profile.country).upper()
        health = availability or {}
        configured = set(profile.payment_rails)
        candidates = self.routes.get((currency, country), tuple(profile.payment_rails))

        for rail in candidates:
            provider = _PROVIDER_BY_RAIL.get(rail, rail)
            if rail not in configured:
                continue
            if health.get(rail, health.get(provider, True)) is False:
                continue
            return RailSelection(
                provider=provider,
                rail=rail,
                currency=currency,
                destination_country=country,
                rationale=f"selected {rail} for {currency}/{country}; configured and healthy",
            )

        considered = ",".join(candidates) or "none"
        raise RailUnavailableError(
            f"no healthy configured rail for {currency}/{country}; considered={considered}"
        )
