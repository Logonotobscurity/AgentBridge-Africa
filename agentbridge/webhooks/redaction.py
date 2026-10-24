"""Fail-closed webhook payload minimization and keyed PII pseudonymization.

The raw request body is authenticated and hashed before this boundary. Only a
small normalized projection is persisted; unknown provider fields are dropped.
Low-entropy identifiers such as MSISDNs use HMAC-SHA256 rather than an unkeyed
hash so an attacker with database access cannot cheaply enumerate phone numbers.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
from dataclasses import dataclass
from typing import Any, Mapping

PII_HMAC_KEY_ENV = "AGENTBRIDGE_PII_HMAC_KEY"
_SAFE_EVENT = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
_PAYSTACK_EVENTS = frozenset({
    "charge.success", "transfer.success", "transfer.failed", "transfer.reversed",
    "refund.processed", "refund.failed",
})
_PAYSTACK_STATUSES = frozenset({"success", "failed", "abandoned", "reversed", "pending", "processing"})
_MTN_STATUSES = frozenset({"successful", "failed", "rejected", "expired", "pending", "ongoing"})
_PHONE_KEYS = frozenset({"phone", "phonenumber", "mobile", "mobilenumber", "msisdn", "partyid"})
_EMAIL_KEYS = frozenset({"email", "emailaddress"})


class WebhookRedactionError(ValueError):
    """Raised when payload minimization cannot be performed safely."""


@dataclass(frozen=True)
class WebhookPayloadSanitizer:
    """Produce a bounded, provider-specific callback evidence projection."""

    hmac_key: bytes
    key_id: str = "primary"

    def __post_init__(self) -> None:
        if len(self.hmac_key) < 32:
            raise WebhookRedactionError("PII HMAC key must contain at least 32 bytes")
        if not _SAFE_EVENT.fullmatch(self.key_id):
            raise WebhookRedactionError("PII HMAC key_id must be a safe identifier")

    @classmethod
    def from_environment(cls, *, environ: Mapping[str, str] | None = None, key_id: str = "primary") -> "WebhookPayloadSanitizer":
        source = os.environ if environ is None else environ
        value = source.get(PII_HMAC_KEY_ENV, "")
        if len(value.encode("utf-8")) < 32:
            raise WebhookRedactionError(f"{PII_HMAC_KEY_ENV} must contain at least 32 bytes")
        return cls(value.encode("utf-8"), key_id=key_id)

    def sanitize(self, provider: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Drop unknown fields and pseudonymize recognized low-entropy PII."""
        provider = provider.lower()
        if provider == "paystack":
            projection = self._paystack(payload)
        elif provider == "mpesa":
            projection = self._mpesa(payload)
        elif provider == "mtn_momo":
            projection = self._mtn_momo(payload)
        else:
            raise WebhookRedactionError("unsupported webhook provider")

        fingerprints = self._pii_fingerprints(payload)
        result: dict[str, Any] = {
            "schema_version": 1,
            "provider": provider,
            **projection,
        }
        if fingerprints:
            result["pii_fingerprints"] = fingerprints
        return result

    def _paystack(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        data = payload.get("data") if isinstance(payload.get("data"), Mapping) else {}
        return {
            "event": _allowlisted_token(payload.get("event"), _PAYSTACK_EVENTS),
            "provider_status": _allowlisted_token(data.get("status"), _PAYSTACK_STATUSES),
        }

    def _mpesa(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        body = payload.get("Body") if isinstance(payload.get("Body"), Mapping) else {}
        callback = body.get("stkCallback") if isinstance(body.get("stkCallback"), Mapping) else {}
        return {"result_code": _bounded_code(callback.get("ResultCode"))}

    def _mtn_momo(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return {"provider_status": _allowlisted_token(payload.get("status"), _MTN_STATUSES)}

    def _pii_fingerprints(self, payload: Mapping[str, Any]) -> list[dict[str, str]]:
        found: set[tuple[str, str]] = set()

        def visit(value: Any, key: str = "") -> None:
            if isinstance(value, Mapping):
                # Daraja callback metadata uses {"Name": "PhoneNumber", "Value": ...}.
                named_kind = _pii_kind(value.get("Name"))
                if named_kind and "Value" in value and _is_scalar(value["Value"]):
                    found.add((named_kind, self._fingerprint(named_kind, value["Value"])))
                party_type = str(value.get("partyIdType") or value.get("PartyIdType") or "").upper()
                for child_key, child in value.items():
                    child_kind = _pii_kind(child_key)
                    if _normalized_key(child_key) == "partyid" and party_type != "MSISDN":
                        child_kind = None
                    if child_kind and _is_scalar(child):
                        found.add((child_kind, self._fingerprint(child_kind, child)))
                    else:
                        visit(child, str(child_key))
            elif isinstance(value, (list, tuple)):
                for child in value:
                    visit(child, key)

        visit(payload)
        return [
            {"kind": kind, "algorithm": "HMAC-SHA256", "key_id": self.key_id, "digest": digest}
            for kind, digest in sorted(found)
        ]

    def _fingerprint(self, kind: str, value: Any) -> str:
        normalized = _normalize_pii(kind, value)
        return hmac.new(self.hmac_key, f"{kind}:{normalized}".encode("utf-8"), hashlib.sha256).hexdigest()


def _normalized_key(value: Any) -> str:
    return "".join(character for character in str(value).lower() if character.isalnum())


def _pii_kind(key: Any) -> str | None:
    normalized = _normalized_key(key)
    if normalized in _PHONE_KEYS:
        return "msisdn"
    if normalized in _EMAIL_KEYS:
        return "email"
    return None


def _normalize_pii(kind: str, value: Any) -> str:
    text = str(value).strip()
    if kind == "msisdn":
        digits = "".join(character for character in text if character.isdigit())
        if not digits:
            raise WebhookRedactionError("MSISDN value cannot be normalized")
        return digits
    if kind == "email":
        normalized = text.casefold()
        if not normalized:
            raise WebhookRedactionError("email value cannot be normalized")
        return normalized
    raise WebhookRedactionError("unsupported PII kind")


def _is_scalar(value: Any) -> bool:
    return value is not None and not isinstance(value, (Mapping, list, tuple, set))


def _allowlisted_token(value: Any, allowed: frozenset[str]) -> str:
    token = str(value or "").strip().lower()
    return token if _SAFE_EVENT.fullmatch(token) and token in allowed else "unknown"


def _bounded_code(value: Any) -> str:
    code = str(value if value is not None else "").strip()
    return code if code.isdigit() and len(code) <= 12 else "unknown"
