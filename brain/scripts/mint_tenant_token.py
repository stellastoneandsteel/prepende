#!/usr/bin/env python3
"""Mint a tenant connector token for the MCP-HTTP cockpit.

One token => one {tenant, workspace, scope, capabilities}. The token DETERMINES
all identity fields and the physical scope
server-side (interface/mcp_scope.token_to_principal), so a connecting client
can only ever act inside its own namespace, whatever it puts in a request body.

Generic by design (SEPARATION): the scope slug is an argument, never baked in.
Default capabilities are a least-privilege operating set — read, propose, chat,
and the knowledge graph — with NO durable-write or approval tools. Pass
--capabilities to widen (comma list, or "safe", or "all").

Usage:
    python3 scripts/mint_tenant_token.py --tenant acme --workspace acme-sales --scope acme--acme-sales
    python3 scripts/mint_tenant_token.py --scope acme-lab --capabilities all
    python3 scripts/mint_tenant_token.py --scope shopname \\
        --capabilities memory_search,knowledge_search,memory_propose,chat

Prints, in order: the canonical PREPENDE_TENANT_TOKENS entry for the cockpit
host and the compatibility ENGRAM_TENANT_TOKEN line current downstream clients
consume. The token is shown once; store it in a password manager.
"""

from __future__ import annotations

import argparse
import json
import re
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from prepende_brain.identity import require_identity_namespace  # noqa: E402

# Mirrors interface/mcp_scope.SAFE_TOOLS — the read + propose surface. Kept as a
# literal here so the minter has no import-time dependency on the kernel.
_SAFE = [
    "chat",
    "pursue_goal",
    "memory_search",
    "memory_propose",
    "memory_candidates",
    "knowledge_search",
    "knowledge_related",
    "account",
]
_WRITE = [
    "remember",
    "memory_reject",
    "run_workflow",
    "list_workflows",
]
_ALL = set(_SAFE) | set(_WRITE)
# A sensible default for a business operating surface: read + propose + graph,
# no writes/approvals. A subset of _SAFE (drops pursue_goal/candidates noise).
_DEFAULT = ["chat", "memory_search", "memory_propose", "knowledge_search", "knowledge_related", "account"]

_SCOPE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def validate_scope(scope: str) -> str:
    s = (scope or "").strip()
    if not _SCOPE_RE.fullmatch(s):
        raise ValueError(f"invalid scope {scope!r}: lowercase slug [a-z0-9_-], 1-64 chars")
    return s


def resolve_capabilities(spec: str) -> "list[str] | str":
    """'all' -> 'all' (passthrough the map understands); 'safe' -> SAFE set;
    '' -> the operating default; otherwise a validated comma list."""
    raw = (spec or "").strip().lower()
    if raw == "all":
        return "all"
    if raw == "safe":
        return list(_SAFE)
    if not raw:
        return list(_DEFAULT)
    caps = [c.strip() for c in raw.split(",") if c.strip()]
    if not caps:
        raise ValueError("capabilities list cannot be empty")
    unknown = sorted(set(caps) - _ALL)
    if unknown:
        raise ValueError(f"unknown MCP capabilities: {unknown}")
    return caps


def build_token_entry(
    scope: str,
    capabilities: "list[str] | str",
    token: str,
    *,
    tenant: str = "",
    workspace: str = "",
) -> dict:
    """Build one rich token entry while retaining the legacy call signature."""
    tenant = validate_scope(tenant or scope)
    workspace = validate_scope(workspace or scope)
    scope = require_identity_namespace(tenant, workspace, scope)
    if not token or len(token) < 16:
        raise ValueError("token must be at least 16 chars")
    if capabilities != "all":
        if not isinstance(capabilities, list) or not capabilities or not all(
            isinstance(capability, str) and capability.strip()
            for capability in capabilities
        ):
            raise ValueError("capabilities must be 'all' or a nonempty string list")
        unknown = sorted(set(capabilities) - _ALL)
        if unknown:
            raise ValueError(f"unknown MCP capabilities: {unknown}")
    return {token: {
        "tenant": tenant,
        "workspace": workspace,
        "scope": scope,
        "capabilities": capabilities,
    }}


def main() -> None:
    ap = argparse.ArgumentParser(description="Mint a tenant connector token.")
    ap.add_argument(
        "--scope", default="",
        help="canonical physical namespace (derived from tenant + workspace when omitted)",
    )
    ap.add_argument("--tenant", default="", help="commercial tenant id (default: scope)")
    ap.add_argument("--workspace", default="", help="workspace id (default: scope)")
    ap.add_argument("--capabilities", default="", help="comma list, or 'safe', or 'all'")
    ap.add_argument("--token", default="", help="use a specific token (default: generate)")
    args = ap.parse_args()

    if not args.scope and (not args.tenant or not args.workspace):
        ap.error("provide --scope for a legacy identity, or both --tenant and --workspace")
    tenant = validate_scope(args.tenant or args.scope)
    workspace = validate_scope(args.workspace or args.scope)
    scope = require_identity_namespace(tenant, workspace, args.scope)
    token = args.token.strip() or f"tok-{scope}-{secrets.token_urlsafe(24)}"
    caps = resolve_capabilities(args.capabilities)
    entry = build_token_entry(
        scope, caps, token, tenant=tenant, workspace=workspace
    )

    print(
        f"# Tenant token for tenant '{tenant}', workspace '{workspace}', "
        f"scope '{scope}' (store the token in a password manager):"
    )
    print()
    print("# 1) On the MCP-HTTP cockpit host, merge into PREPENDE_TENANT_TOKENS:")
    print(f"PREPENDE_TENANT_TOKENS={json.dumps(entry)}")
    print()
    print("# 2) On the downstream site/client (e.g. Netlify), set:")
    print(f"ENGRAM_TENANT_TOKEN={token}")
    print("# ENGRAM_API_URL=https://<your-cockpit-host>/  (the MCP-HTTP endpoint)")
    print("# The downstream ENGRAM_* names are a compatibility contract; the brain is Prepende.")


if __name__ == "__main__":
    main()
