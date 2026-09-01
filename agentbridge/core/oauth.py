"""OAuth 2.1 + PKCE for remote MCP tool endpoints.

Remote tools are not unauthenticated proxies. Every payment execution requires:
- identified confidential/public client
- PKCE S256 code exchange
- exact redirect URI match
- access token bound to least-privilege scopes
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time
from dataclasses import dataclass, field
from typing import Iterable
from urllib.parse import urlparse

READ_SCOPES = frozenset({"payments:quote", "payments:status", "compliance:read"})
WRITE_SCOPES = frozenset({"payments:execute", "compliance:write", "admin:budget"})
ALL_SCOPES = READ_SCOPES | WRITE_SCOPES


class OAuthError(ValueError):
    pass


@dataclass(frozen=True)
class TokenClaims:
    client_id: str
    scopes: frozenset[str]
    exp: float
    redirect_uri: str
    token_id: str


@dataclass
class _AuthCode:
    client_id: str
    redirect_uri: str
    code_challenge: str
    scopes: frozenset[str]
    exp: float


@dataclass
class OAuth21Provider:
    issuer: str = "https://agentbridge.africa"
    code_ttl_s: int = 120
    token_ttl_s: int = 900
    allowed_redirects: set[str] = field(default_factory=set)
    _codes: dict[str, _AuthCode] = field(default_factory=dict)
    _tokens: dict[str, TokenClaims] = field(default_factory=dict)
    _secret: bytes = field(default_factory=lambda: os.urandom(32))

    @staticmethod
    def generate_pkce() -> tuple[str, str]:
        """Return (code_verifier, code_challenge) using S256."""
        verifier = secrets.token_urlsafe(64)
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
        return verifier, challenge

    def authorize(
        self,
        *,
        client_id: str,
        redirect_uri: str,
        code_challenge: str,
        code_challenge_method: str = "S256",
        scopes: Iterable[str] = ("payments:quote",),
    ) -> str:
        if code_challenge_method != "S256":
            raise OAuthError("only S256 PKCE is supported (OAuth 2.1)")
        self._assert_redirect(redirect_uri)
        scope_set = frozenset(scopes)
        unknown = scope_set - ALL_SCOPES
        if unknown:
            raise OAuthError(f"unknown scopes: {sorted(unknown)}")
        code = secrets.token_urlsafe(32)
        self._codes[code] = _AuthCode(
            client_id=client_id,
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
            scopes=scope_set,
            exp=time.time() + self.code_ttl_s,
        )
        return code

    def exchange(
        self,
        *,
        code: str,
        code_verifier: str,
        redirect_uri: str,
        client_id: str,
    ) -> str:
        record = self._codes.pop(code, None)
        if record is None or record.exp < time.time():
            raise OAuthError("invalid or expired authorization code")
        if record.client_id != client_id:
            raise OAuthError("client_id mismatch")
        if record.redirect_uri != redirect_uri:
            raise OAuthError("redirect_uri mismatch")
        self._assert_redirect(redirect_uri)
        expected = hashlib.sha256(code_verifier.encode("ascii")).digest()
        expected_b64 = base64.urlsafe_b64encode(expected).rstrip(b"=").decode("ascii")
        if not hmac.compare_digest(expected_b64, record.code_challenge):
            raise OAuthError("pkce verification failed")
        token = secrets.token_urlsafe(32)
        claims = TokenClaims(
            client_id=client_id,
            scopes=record.scopes,
            exp=time.time() + self.token_ttl_s,
            redirect_uri=redirect_uri,
            token_id=hashlib.sha256(token.encode()).hexdigest()[:16],
        )
        self._tokens[token] = claims
        return token

    def introspect(self, token: str) -> TokenClaims:
        claims = self._tokens.get(token)
        if claims is None or claims.exp < time.time():
            self._tokens.pop(token, None)
            raise OAuthError("invalid or expired access token")
        return claims

    def _assert_redirect(self, redirect_uri: str) -> None:
        parsed = urlparse(redirect_uri)
        if parsed.scheme not in {"https", "http"} or not parsed.netloc:
            raise OAuthError("redirect_uri must be an absolute http(s) URI")
        if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost"}:
            raise OAuthError("http redirect_uri is only allowed on loopback")
        if self.allowed_redirects and redirect_uri not in self.allowed_redirects:
            raise OAuthError("redirect_uri is not registered for this client")


def scope_allows(token: TokenClaims, needed: str) -> bool:
    if needed in token.scopes:
        return True
    if needed in READ_SCOPES and "admin:budget" in token.scopes:
        return True
    return False
