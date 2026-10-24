"""Provider-neutral, checkpoint-safe connector boundary models."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Currency(StrEnum):
    KES = "KES"
    NGN = "NGN"
    GHS = "GHS"
    UGX = "UGX"
    TZS = "TZS"
    ZAR = "ZAR"
    USD = "USD"


class Country(StrEnum):
    KE = "KE"
    NG = "NG"
    GH = "GH"
    UG = "UG"
    TZ = "TZ"
    ZA = "ZA"


class ConnectorStatus(StrEnum):
    SUBMITTED = "SUBMITTED"
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    FAILED = "FAILED"


class PaymentIntent(BaseModel):
    """Per-transaction data, deliberately separate from ContextProfile."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    intent_id: UUID = Field(default_factory=uuid4)
    amount: Decimal
    currency: Currency
    source_country: Country
    destination_country: Country
    recipient_ref: str = Field(min_length=3, max_length=160)
    merchant_reference: str = Field(min_length=1, max_length=100)
    idempotency_key_ref: str = Field(min_length=8, max_length=128)

    @field_validator("amount", mode="before")
    @classmethod
    def exact_positive_amount(cls, value: Any) -> Decimal:
        if isinstance(value, float):
            raise ValueError("floating-point amounts are forbidden at the provider boundary")
        try:
            amount = Decimal(value)
        except (InvalidOperation, TypeError) as exc:
            raise ValueError("amount must be an exact decimal value") from exc
        if not amount.is_finite() or amount <= 0:
            raise ValueError("amount must be finite and positive")
        return amount


class ConnectorResult(BaseModel):
    """Normalized result; provider payloads stay outside graph/checkpoint state."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str
    status: ConnectorStatus
    provider_reference: str
    transaction_id: str | None = None
    provider_code: str | None = None
    error_code: str | None = None
    requires_reconciliation: bool = False
