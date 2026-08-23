"""Bearer-auth + rate-limit middleware for the MCP HTTP transport (threats T1, T10).

Wraps the FastMCP `streamable_http_app()` (a Starlette ASGI app). On every HTTP
request it:
  1. reads `Authorization: Bearer <token>`;
  2. resolves the token to a {tenant, workspace, scope, capabilities} principal
     (mcp_scope.token_to_principal) — missing/invalid -> 401;
  3. enforces a per-token rate limit (mcp_scope.RateLimiter) -> 429;
  4. sets the request-scoped principal context-var so `_scope()` and the capability
     guard honor it for THIS request, then clears it after the response.

PURE ASGI (not Starlette BaseHTTPMiddleware) on purpose: the principal is a
contextvar, and a pure-ASGI wrapper runs the downstream app in the SAME task, so the
contextvar propagates to the MCP tool dispatch. BaseHTTPMiddleware runs the app in a
separate anyio task and would lose it.

Imports ONLY mcp_scope (no FastMCP), so the security logic is testable with Starlette's
TestClient alone — see tests/smoke_mcp_http_auth.py.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Iterable

from interface import mcp_scope


async def _send_json(send: Callable, status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


def _bearer(headers: Iterable) -> str:
    for k, v in headers:
        if k.decode().lower() == "authorization":
            val = v.decode()
            return val[7:].strip() if val[:7].lower() == "bearer " else ""
    return ""


def auth_middleware(app: Callable, rate_limiter: Any = None,
                    exempt_paths: Iterable[str] = ()) -> Callable:
    """Wrap an ASGI app with per-call bearer auth + rate limiting. Returns an ASGI3
    callable. `rate_limiter` defaults to a fresh RateLimiter (env-configured)."""
    limiter = rate_limiter if rate_limiter is not None else mcp_scope.RateLimiter()
    exempt = set(exempt_paths)

    async def middleware(scope: dict, receive: Callable, send: Callable) -> None:
        if scope.get("type") != "http" or scope.get("path", "") in exempt:
            await app(scope, receive, send)
            return
        token = _bearer(scope.get("headers", []))
        principal = mcp_scope.token_to_principal(token)
        if principal is None:
            await _send_json(send, 401, {"error": "unauthorized: missing or invalid bearer token"})
            return
        if not limiter.allow(token):
            await _send_json(send, 429, {"error": "rate limit exceeded"})
            return
        marker = mcp_scope.set_principal(principal)
        try:
            await app(scope, receive, send)
        finally:
            mcp_scope.reset_principal(marker)

    return middleware
