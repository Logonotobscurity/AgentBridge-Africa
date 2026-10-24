"""Allowlisted capability-pack registry; never imports code from transaction data."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from agentbridge.payments.base import PaymentProviderAdapter

ALLOWED_CAPABILITY_PACKS = frozenset({"ke-payments", "ng-payments", "gh-payments", "ug-payments"})


@dataclass
class CapabilityPackRegistry:
    _adapters: dict[str, PaymentProviderAdapter] = field(default_factory=dict)

    def register(self, adapter_id: str, adapter: PaymentProviderAdapter) -> None:
        if adapter_id not in ALLOWED_CAPABILITY_PACKS:
            raise ValueError(f"capability pack is not allowlisted: {adapter_id}")
        if adapter_id in self._adapters:
            raise ValueError(f"capability pack already registered: {adapter_id}")
        self._adapters[adapter_id] = adapter

    def resolve(self, adapter_id: str) -> PaymentProviderAdapter:
        try:
            return self._adapters[adapter_id]
        except KeyError as exc:
            raise LookupError(f"capability pack is unavailable: {adapter_id}") from exc

    @property
    def adapters(self) -> Mapping[str, PaymentProviderAdapter]:
        return dict(self._adapters)
