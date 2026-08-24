"""MCP scope policy — the bits of the inbound-MCP trust contract that must be
testable without importing the (py>=3.10) `mcp` package.

For stdio, the MCP server binds one process to one scope via
PREPENDE_MCP_SCOPE (``ENGRAM_MCP_SCOPE`` remains a compatibility alias), and
no tool accepts a scope/tenant parameter (the anti-spoof property). The one
foot-gun is a stdio deploy that declares tenant tokens but forgets to pin a
scope: every client would then silently bind to "default".
`startup_scope_guard` refuses to boot in exactly that case (threat T15 in
docs/OPENCLAW-ENGRAM-CONNECTOR.md). HTTP uses per-call bearer principals to
bind tenant, workspace, physical scope, and capabilities (threat T1).

Stdlib only.
"""

from __future__ import annotations

import contextvars
import hashlib
import json
import os
import re
import time
from typing import Any, Mapping

from prepende_brain.identity import require_identity_namespace
from prepende_brain.env import brand_env

try:
    from private_extensions import mcp_tools as _private_mcp_tools
except ModuleNotFoundError as exc:
    if not (exc.name or "").startswith("private_extensions"):
        raise
    _private_mcp_tools = None

# ---- capability scoping (threat T2) -----------------------------------------
# Least-privilege per connection. The READ/PROPOSE set is safe for an untrusted
# agent; the WRITE/ACTION set must be opt-in. Candidate approval and knowledge
# import are intentionally not MCP capabilities at all: owners use the separate
# approval surface and reviewed-bundle CLI. Default (unset) = every registered
# MCP tool; an operator running an untrusted client uses ``safe`` or a comma-list.
PRIVATE_SAFE_TOOLS = (
    frozenset(_private_mcp_tools.SAFE_TOOLS)
    if _private_mcp_tools is not None
    else frozenset()
)
PRIVATE_WRITE_TOOLS = (
    frozenset(getattr(_private_mcp_tools, "WRITE_TOOLS", ()))
    if _private_mcp_tools is not None
    else frozenset()
)
OPERATOR_SAFE_TOOLS = frozenset({
    "operator_status",
})
OPERATOR_WRITE_TOOLS = frozenset({
    "operator_start", "operator_finish",
})

SAFE_TOOLS = frozenset({
    "chat", "pursue_goal", "memory_search", "memory_propose",
    "memory_candidates", "knowledge_search", "knowledge_related", "account",
    *PRIVATE_SAFE_TOOLS,
    *OPERATOR_SAFE_TOOLS,
})
WRITE_TOOLS = frozenset({
    "remember", "memory_reject", "run_workflow", "list_workflows",
    *OPERATOR_WRITE_TOOLS,
    *PRIVATE_WRITE_TOOLS,
})
ALL_TOOLS = SAFE_TOOLS | WRITE_TOOLS


def _brand_env(env: Mapping[str, str], suffix: str) -> str:
    """Read the canonical Prepende env first, then the Engram alias."""
    return brand_env(suffix, env=env)


def allowed_capabilities(env: Mapping[str, str] | None = None) -> set[str]:
    """The tool names this connection may call, from PREPENDE_MCP_CAPABILITIES:
      unset/empty        -> ALL_TOOLS (operator default; no behaviour change)
      'untrusted'/'safe' -> SAFE_TOOLS (read + propose; no writes/actions)
      'all'              -> ALL_TOOLS
      'a, b, c'          -> those registered tools (unknown names grant nothing)
    """
    e = os.environ if env is None else env
    raw = _brand_env(e, "MCP_CAPABILITIES")
    if not raw:
        return set(ALL_TOOLS)
    low = raw.lower()
    if low in ("untrusted", "safe"):
        return set(SAFE_TOOLS)
    if low == "all":
        return set(ALL_TOOLS)
    requested = {t.strip() for t in raw.split(",") if t.strip()}
    return requested & set(ALL_TOOLS)


def is_allowed(tool: str, env: Mapping[str, str] | None = None) -> bool:
    """A request-scoped PRINCIPAL (set by the HTTP auth middleware, threat T1) wins:
    its per-token capabilities decide. Without one (stdio), fall back to the
    per-process PREPENDE_MCP_CAPABILITIES (or its Engram alias)."""
    p = current_principal()
    if p is not None:
        return tool in (p.get("capabilities") or set())
    return tool in allowed_capabilities(env)


# ---- per-call principal (threat T1: HTTP per-call auth) ----------------------
# One token => one {tenant, workspace, scope, capabilities}. The token determines
# every identity field; nothing in the request body can override it (no tool takes
# a scope param — see S2). HTTP middleware sets this context-var per request;
# stdio leaves it unset (env decides).
_PRINCIPAL: contextvars.ContextVar = contextvars.ContextVar("engram_mcp_principal", default=None)

_IDENTITY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_REVISION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _identity_slug(value: Any) -> str | None:
    """Normalize one tenant/workspace/scope identifier without filesystem I/O."""

    if not isinstance(value, str):
        return None
    slug = value.strip()
    return slug if _IDENTITY_RE.fullmatch(slug) else None


def deployment_revision(env: Mapping[str, str] | None = None) -> str | None:
    """Return a sanitized host-owned deployment revision.

    ``None`` means the host did not configure a revision. Invalid configured
    values also return ``None`` and are separately refused by the startup guard.
    """

    e = os.environ if env is None else env
    raw = _brand_env(e, "DEPLOYMENT_REVISION")
    if not raw:
        return None
    return raw if _REVISION_RE.fullmatch(raw) else None


def token_value_scope(value: Any) -> str | None:
    """Return the physical memory/vault scope represented by a token-map value.

    This is intentionally public so legacy /v1 auth and owner executors can
    consume the same richer token map as MCP. Bare strings and old
    ``{"scope": ...}`` entries remain valid; a rich identity defaults its
    physical scope to the canonical tenant/workspace namespace when ``scope``
    is omitted; an explicitly different namespace is refused.
    """

    if isinstance(value, str):
        return _identity_slug(value)
    if not isinstance(value, dict):
        return None
    if "tenant" in value or "workspace" in value:
        tenant = _identity_slug(value.get("tenant"))
        workspace = _identity_slug(value.get("workspace"))
        if not tenant or not workspace:
            return None
        try:
            return require_identity_namespace(
                tenant, workspace, value.get("scope") or ""
            )
        except ValueError:
            return None
    return _identity_slug(value.get("scope"))


def _token_value_principal(value: Any) -> dict[str, Any] | None:
    """Validate one legacy or rich token-map value and build its principal."""

    if isinstance(value, str):
        scope = _identity_slug(value)
        if not scope:
            return None
        return {
            "tenant": scope,
            "workspace": scope,
            "scope": scope,
            "capabilities": set(SAFE_TOOLS),
        }
    if not isinstance(value, dict):
        return None

    scope = token_value_scope(value)
    if not scope:
        return None
    has_rich_identity = "tenant" in value or "workspace" in value
    if has_rich_identity:
        tenant = _identity_slug(value.get("tenant"))
        workspace = _identity_slug(value.get("workspace"))
        if not tenant or not workspace:
            return None
    else:
        # Compatibility: the original token contract carried only one scope.
        tenant = workspace = scope

    caps = value.get("capabilities")
    if isinstance(caps, list):
        if not all(isinstance(cap, str) and cap.strip() for cap in caps):
            return None
        cset = {cap.strip() for cap in caps}
        if not cset.issubset(ALL_TOOLS):
            return None
    elif isinstance(caps, str) and caps.lower() == "all":
        cset = set(ALL_TOOLS)
    elif isinstance(caps, str) and caps.lower() in {"safe", "untrusted"}:
        cset = set(SAFE_TOOLS)
    elif caps is not None:
        return None
    else:
        cset = set(SAFE_TOOLS)
    return {
        "tenant": tenant,
        "workspace": workspace,
        "scope": scope,
        "capabilities": cset,
    }


def token_to_principal(token: str, env: Mapping[str, str] | None = None) -> dict[str, Any] | None:
    """Resolve a connector token to a server-controlled identity principal.

    Rich entries carry ``tenant``, ``workspace``, physical ``scope``, and
    capabilities. Bare strings and old ``{"scope": ...}`` entries map all
    three identity fields to that scope, preserving legacy behavior. Invalid
    or partial rich identities fail closed (the middleware returns 401).
    """
    token = (token or "").strip()
    if not token:
        return None
    e = os.environ if env is None else env
    raw = _brand_env(e, "TENANT_TOKENS")
    if not raw:
        return None
    try:
        m = json.loads(raw)
    except Exception:
        return None
    if not isinstance(m, dict) or token not in m:
        return None
    principal = _token_value_principal(m[token])
    if principal is None:
        return None
    fingerprint = "sha256:" + hashlib.sha256(token.encode("utf-8")).hexdigest()
    return {
        **principal,
        "principalId": "mcp-token:" + fingerprint,
        "principalFingerprint": fingerprint,
        "principalType": "bearer_token",
    }


def configured_physical_scopes(
    memory_scope: str = "", env: Mapping[str, str] | None = None
) -> list[str]:
    """Return configured physical namespaces without importing an executor.

    Token identity parsing stays centralized here so clean distributions can
    verify rich-token consumers without packaging an operator-only action
    executor. Invalid token entries contribute no authority.
    """

    e = os.environ if env is None else env
    scopes: set[str] = set()
    raw = _brand_env(e, "TENANT_TOKENS")
    if raw:
        try:
            token_map = json.loads(raw)
            if isinstance(token_map, dict):
                scopes.update(
                    principal["scope"]
                    for value in token_map.values()
                    if (principal := _token_value_principal(value)) is not None
                )
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    configured_memory_scope = _identity_slug(memory_scope)
    if configured_memory_scope:
        scopes.add(configured_memory_scope)
    return sorted(scopes)


def set_principal(principal: dict[str, Any] | None):
    return _PRINCIPAL.set(principal)


def reset_principal(token) -> None:
    _PRINCIPAL.reset(token)


def current_principal() -> dict[str, Any] | None:
    return _PRINCIPAL.get()


# ---- per-token rate limiting (threat T10) -----------------------------------
class RateLimiter:
    """Sliding-window limiter, N requests per 60s per key. `now` is injectable so
    it's testable without sleeping. Default from PREPENDE_MCP_RATE_LIMIT_PER_MINUTE."""

    def __init__(self, per_minute: int | None = None) -> None:
        self.per_minute = int(per_minute if per_minute is not None
                              else (_brand_env(os.environ, "MCP_RATE_LIMIT_PER_MINUTE") or 120))
        self._hits: dict[str, list[float]] = {}

    def allow(self, key: str, now: float | None = None) -> bool:
        if self.per_minute <= 0:
            return True
        t = time.monotonic() if now is None else now
        window = [h for h in self._hits.get(key, ()) if h > t - 60.0]
        if len(window) >= self.per_minute:
            self._hits[key] = window
            return False
        window.append(t)
        self._hits[key] = window
        return True


def startup_scope_guard(env: Mapping[str, str] | None = None) -> str | None:
    """Return a refusal message if the MCP server is configured in a way that
    would silently bind every client to the 'default' scope, else None.

    Trips when PREPENDE_TENANT_TOKENS (or its legacy alias) declares any
    non-default scope but PREPENDE_MCP_SCOPE/ENGRAM_MCP_SCOPE is unset — i.e.
    the operator clearly intends multiple tenants, but the stdio server can
    only honour one scope per process and none is pinned. HTTP binds identity
    per bearer request and therefore needs no process-wide scope pin. Invalid
    token maps are refused for both transports.
    """
    e = os.environ if env is None else env
    pinned = _brand_env(e, "MCP_SCOPE")
    if pinned and not _identity_slug(pinned):
        return "PREPENDE_MCP_SCOPE is not a validated lowercase tenant slug."
    configured_revision = _brand_env(e, "DEPLOYMENT_REVISION")
    if configured_revision and deployment_revision(e) is None:
        return (
            "PREPENDE_DEPLOYMENT_REVISION must be a sanitized revision token "
            "([A-Za-z0-9._-], 1-128 characters)."
        )

    process_tenant = _brand_env(e, "MCP_TENANT")
    process_workspace = _brand_env(e, "MCP_WORKSPACE")
    if process_tenant or process_workspace:
        if not process_tenant or not process_workspace:
            return (
                "PREPENDE_MCP_TENANT and PREPENDE_MCP_WORKSPACE must be set together."
            )
        try:
            expected_process_scope = require_identity_namespace(
                process_tenant, process_workspace, pinned
            )
        except ValueError:
            return (
                "PREPENDE_MCP_SCOPE does not match the canonical namespace for "
                "PREPENDE_MCP_TENANT + PREPENDE_MCP_WORKSPACE."
            )
        if not pinned:
            pinned = expected_process_scope
    raw = _brand_env(e, "TENANT_TOKENS")
    if not raw:
        return None
    try:
        token_map = json.loads(raw)
        if not isinstance(token_map, dict):
            raise ValueError("token map must be an object")
        scopes = set()
        for token, value in token_map.items():
            if not str(token).strip():
                raise ValueError("token map contains an empty token identity")
            scope = token_value_scope(value)
            if not scope or _token_value_principal(value) is None:
                raise ValueError("token map contains an invalid identity")
            scopes.add(scope)
    except Exception:
        return (
            "PREPENDE_TENANT_TOKENS is not a valid token map. Use "
            "{\"token\": \"scope\"} or {\"token\": {\"tenant\": \"...\", "
            "\"workspace\": \"...\", \"scope\": \"...\"}}."
        )
    transport = (_brand_env(e, "MCP_TRANSPORT") or "stdio").lower()
    if transport == "http":
        return None
    non_default = {s for s in scopes if s and s != "default"}
    if non_default and not pinned:
        return (
            "PREPENDE_TENANT_TOKENS declares scopes %s but PREPENDE_MCP_SCOPE is unset "
            "(legacy ENGRAM_MCP_SCOPE is also accepted). "
            "The stdio MCP server binds ONE scope per process; without a pin every client "
            "would silently bind to 'default'. Set PREPENDE_MCP_SCOPE=<one-scope> per instance "
            "(Rung-2 co-located stdio), or use HTTP with mandatory per-call bearer principals."
            % sorted(non_default)
        )
    if pinned and scopes and pinned not in scopes:
        return (
            "PREPENDE_MCP_SCOPE does not match any physical scope declared by "
            "PREPENDE_TENANT_TOKENS for this stdio process."
        )
    if len(scopes) > 1:
        return (
            "A stdio MCP process may bind exactly one physical scope; use one token "
            "scope per process or HTTP bearer principals."
        )
    return None
