"""Bearer token authentication for the MCP endpoint.

A pure-ASGI middleware guards every request except the health probe. It never
buffers the response body, so it is safe in front of the MCP streamable-HTTP
app. Tokens are read per request so operators can rotate them without a restart
and so tests can substitute their own provider.
"""
from __future__ import annotations

import json
import secrets
from contextvars import ContextVar
from hashlib import sha256
from typing import Awaitable, Callable

from .projects_api import ApiSettings

REALM = "Extractor 1C MCP"
authenticated_principal: ContextVar[str] = ContextVar("authenticated_principal", default="")


def parse_tokens(raw: str) -> set[str]:
    """Parse "token1, label:token2" into the set of accepted tokens.

    A bare entry is the token itself. A "label:token" entry keeps an operator
    label for readability; only the token part is matched. Tokens generated with
    ``secrets.token_urlsafe`` never contain ':', so the first ':' splits cleanly.
    """
    tokens: set[str] = set()
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        label, sep, rest = entry.partition(":")
        token = rest if sep else label
        if token:
            tokens.add(token)
    return tokens


def current_tokens() -> set[str]:
    """Default provider: read the configured tokens from the environment/.env."""
    return parse_tokens(ApiSettings().mcp_tokens)


def _extract_bearer(header: str) -> str | None:
    scheme, _, value = header.partition(" ")
    value = value.strip()
    if scheme.lower() != "bearer" or not value:
        return None
    return value


def _authenticate(header: str, tokens: set[str]) -> bool:
    presented = _extract_bearer(header)
    if presented is None:
        return False
    presented_bytes = presented.encode()
    # Compare against every accepted token without short-circuiting so the work
    # does not reveal, by timing, how many tokens were tried before a match.
    matched = False
    for token in tokens:
        if secrets.compare_digest(presented_bytes, token.encode()):
            matched = True
    return matched


class BearerAuthMiddleware:
    """Require a Bearer token on all HTTP paths except the exempt ones."""

    def __init__(self, app, provider: Callable[[], set[str]] | None = None,
                 exempt: tuple[str, ...] = ("/health",)) -> None:
        self.app = app
        self._provider = provider
        self.exempt = set(exempt)

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or scope.get("path") in self.exempt:
            await self.app(scope, receive, send)
            return

        tokens = (self._provider or current_tokens)()
        if not tokens:
            await self._reject(send, 503, "MCP authentication is not configured")
            return

        header = ""
        for name, value in scope.get("headers", []):
            if name == b"authorization":
                header = value.decode("latin-1")
                break

        if not _authenticate(header, tokens):
            await self._reject(send, 401, "Invalid or missing bearer token",
                               challenge=True)
            return

        principal = authenticated_principal.set(sha256(_extract_bearer(header).encode()).hexdigest())
        try:
            await self.app(scope, receive, send)
        finally:
            authenticated_principal.reset(principal)

    async def _reject(self, send: Callable[[dict], Awaitable[None]], status: int,
                      message: str, challenge: bool = False) -> None:
        body = json.dumps({"error": message}).encode("utf-8")
        headers = [(b"content-type", b"application/json")]
        if challenge:
            headers.append((b"www-authenticate", f'Bearer realm="{REALM}"'.encode("ascii")))
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})
