"""Provider webhook authentication without persisting shared secrets."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import Mapping, Protocol


class WebhookAuthenticationError(PermissionError):
    pass


class WebhookVerifier(Protocol):
    def verify(self, body: bytes, headers: Mapping[str, str]) -> None: ...


def _headers(headers: Mapping[str, str]) -> dict[str, str]:
    return {key.lower(): value for key, value in headers.items()}


@dataclass(frozen=True)
class PaystackSignatureVerifier:
    secret: bytes

    def verify(self, body: bytes, headers: Mapping[str, str]) -> None:
        supplied = _headers(headers).get("x-paystack-signature", "")
        expected = hmac.new(self.secret, body, hashlib.sha512).hexdigest()
        if not supplied or not hmac.compare_digest(supplied, expected):
            raise WebhookAuthenticationError("invalid Paystack signature")


@dataclass(frozen=True)
class SharedTokenVerifier:
    """Verify a provider-specific callback token using constant-time comparison."""

    token: str
    header: str = "x-callback-token"

    def verify(self, body: bytes, headers: Mapping[str, str]) -> None:
        del body
        supplied = _headers(headers).get(self.header.lower(), "")
        if not supplied or not hmac.compare_digest(supplied, self.token):
            raise WebhookAuthenticationError("invalid callback token")


@dataclass(frozen=True)
class SpiffeProxyVerifier:
    """Trust an SVID identity asserted by the terminating workload proxy.

    The proxy must strip inbound identity headers and set the verification
    marker only after validating mTLS. Never expose this handler directly.
    """

    allowed_ids: frozenset[str]
    identity_header: str = "x-spiffe-id"
    marker_header: str = "x-agentbridge-mtls-verified"

    def verify(self, body: bytes, headers: Mapping[str, str]) -> None:
        del body
        normalized = _headers(headers)
        identity = normalized.get(self.identity_header.lower(), "")
        marker = normalized.get(self.marker_header.lower(), "").lower()
        if marker != "true" or identity not in self.allowed_ids:
            raise WebhookAuthenticationError("unverified SPIFFE workload identity")
