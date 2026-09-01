"""Deployment-owned endpoints, secrets, transport, and effective-dated limits."""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any, Mapping, Protocol
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, SecretStr

from agentbridge.payments.models import Currency, PaymentIntent


class Environment(StrEnum):
    SANDBOX = "sandbox"
    PRODUCTION = "production"


ALLOWLIST_ENDPOINTS: dict[str, dict[Environment, str]] = {
    "daraja": {
        Environment.SANDBOX: "https://sandbox.safaricom.co.ke",
        Environment.PRODUCTION: "https://api.safaricom.co.ke",
    },
    "paystack": {
        Environment.SANDBOX: "https://api.paystack.co",
        Environment.PRODUCTION: "https://api.paystack.co",
    },
    "mtn_momo": {
        Environment.SANDBOX: "https://sandbox.momodeveloper.mtn.com",
        Environment.PRODUCTION: "https://proxy.momoapi.mtn.com",
    },
}


class ProviderSecrets(BaseModel):
    """Ephemeral secret values. Never attach this object to AgentState."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    values: dict[str, SecretStr]

    def reveal(self, key: str) -> str:
        try:
            value = self.values[key].get_secret_value()
        except KeyError as exc:
            raise RuntimeError(f"required provider secret is unavailable: {key}") from exc
        if not value:
            raise RuntimeError(f"required provider secret is unavailable: {key}")
        return value


class SecretResolver(Protocol):
    def resolve(self, provider: str, names: tuple[str, ...]) -> ProviderSecrets: ...


class RecipientResolver(Protocol):
    def resolve(self, recipient_ref: str) -> str:
        """Resolve a tokenized recipient only at the connector boundary."""
        ...


@dataclass(frozen=True)
class MappingRecipientResolver:
    """Small resolver for tests; production should inject a token-vault client."""

    recipients: Mapping[str, str]

    def resolve(self, recipient_ref: str) -> str:
        try:
            return self.recipients[recipient_ref]
        except KeyError as exc:
            raise RuntimeError("recipient reference is unavailable") from exc


@dataclass(frozen=True)
class RuntimeSecretsManager:
    """Resolve secrets per call; environment is the default deployment adapter.

    Production can inject a Vault/KMS resolver implementing ``SecretResolver``.
    SecretStr redacts representation only; callers must still never log revealed
    values or retain this object in graph state.
    """

    environ: Mapping[str, str] | None = None

    def resolve(self, provider: str, names: tuple[str, ...]) -> ProviderSecrets:
        source = self.environ if self.environ is not None else os.environ
        prefix = provider.upper().replace("-", "_")
        values: dict[str, SecretStr] = {}
        for name in names:
            env_name = f"{prefix}_{name.upper()}"
            raw = source.get(env_name, "")
            if not raw:
                raise RuntimeError(f"required provider secret is unavailable: {env_name}")
            values[name] = SecretStr(raw)
        return ProviderSecrets(values=values)


@dataclass(frozen=True)
class PaymentLimit:
    minimum: Decimal
    maximum: Decimal
    policy_version: str


DEFAULT_LIMITS: dict[tuple[str, Currency], PaymentLimit] = {
    ("daraja", Currency.KES): PaymentLimit(Decimal("1.00"), Decimal("150000.00"), "2026-09-default"),
    ("paystack", Currency.NGN): PaymentLimit(Decimal("1.00"), Decimal("5000000.00"), "2026-09-default"),
    ("mtn_momo", Currency.GHS): PaymentLimit(Decimal("0.01"), Decimal("5000000.00"), "2026-09-default"),
    ("mtn_momo", Currency.UGX): PaymentLimit(Decimal("1.00"), Decimal("5000000.00"), "2026-09-default"),
}


def validate_limit(provider: str, intent: PaymentIntent, limits: Mapping[tuple[str, Currency], PaymentLimit]) -> PaymentLimit:
    try:
        limit = limits[(provider, intent.currency)]
    except KeyError as exc:
        raise ValueError(f"unsupported provider/currency corridor: {provider}/{intent.currency}") from exc
    if not limit.minimum <= intent.amount <= limit.maximum:
        raise ValueError(
            f"amount outside {provider} policy bounds for {intent.currency}; policy={limit.policy_version}"
        )
    return limit


def validate_callback_url(url: str, allowed_hosts: frozenset[str]) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("callback URL must be an HTTPS deployment URL without userinfo")
    if parsed.hostname not in allowed_hosts:
        raise ValueError("callback URL host is not allowlisted")
    return url


class TransportError(RuntimeError):
    """Redacted network failure; deliberately carries no provider response body."""


@dataclass(frozen=True)
class HTTPResponse:
    status_code: int
    data: Mapping[str, Any]


class AsyncTransport(Protocol):
    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        json: Mapping[str, Any] | None = None,
        timeout: float = 10.0,
    ) -> HTTPResponse: ...


@dataclass
class HttpxTransport:
    """Non-blocking transport with redirects disabled to preserve the URL allowlist."""

    async def request(self, method: str, url: str, *, headers=None, json=None, timeout=10.0) -> HTTPResponse:
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - production extra
            raise RuntimeError("install agentbridge-africa[connectors]") from exc
        try:
            async with httpx.AsyncClient(follow_redirects=False, timeout=timeout) as client:
                response = await client.request(method, url, headers=headers, json=json)
        except httpx.HTTPError as exc:
            raise TransportError("provider transport failure") from exc
        try:
            data = response.json()
        except ValueError:
            data = {}
        return HTTPResponse(status_code=response.status_code, data=data if isinstance(data, Mapping) else {})


def basic_auth(username: str, password: str) -> str:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return f"Basic {token}"
