"""Mint-tenant-token smoke — token entry shape + scope/capability rules. Zero infra.
    python tests/smoke_mint_tenant_token.py
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.mint_tenant_token import build_token_entry, resolve_capabilities, validate_scope  # noqa: E402


def main() -> None:
    # scope validation: slugs only, no traversal/empties
    assert validate_scope("acme-lab") == "acme-lab"
    for bad in ("", "  ", "../evil", "a/b", "UPPER", "a" * 65, "."):
        try:
            validate_scope(bad)
            raise AssertionError(f"scope {bad!r} should have been rejected")
        except ValueError:
            pass

    # capability resolution
    assert resolve_capabilities("all") == "all"
    assert "memory_search" in resolve_capabilities("safe")
    assert "chat" in resolve_capabilities("")  # operating default
    assert resolve_capabilities("memory_search, knowledge_search") == ["memory_search", "knowledge_search"]
    try:
        resolve_capabilities("account,not_a_tool")
        raise AssertionError("unknown capabilities should have been rejected")
    except ValueError:
        pass
    # default is least-privilege: no durable-write / approval tools
    default = resolve_capabilities("")
    for forbidden in ("remember", "memory_approve", "ingest_knowledge", "run_workflow"):
        assert forbidden not in default, f"{forbidden} must not be in the default token"
    for removed in ("memory_approve", "ingest_knowledge"):
        try:
            resolve_capabilities(removed)
            raise AssertionError(f"removed MCP capability {removed} should be rejected")
        except ValueError:
            pass

    # token entry shape matches interface/mcp_scope.token_to_principal expectations:
    # { "<token>": {"tenant", "workspace", "scope", "capabilities"} }
    entry = build_token_entry("acme-lab", ["memory_search", "chat"], "tok-acme-lab-abcdef0123456789")
    assert list(entry.keys()) == ["tok-acme-lab-abcdef0123456789"]
    val = entry["tok-acme-lab-abcdef0123456789"]
    assert val["tenant"] == "acme-lab"
    assert val["workspace"] == "acme-lab"
    assert val["scope"] == "acme-lab"
    assert val["capabilities"] == ["memory_search", "chat"]
    # round-trips as JSON (it becomes an env var)
    assert json.loads(json.dumps(entry)) == entry

    # 'all' capabilities pass through
    allentry = build_token_entry("acme-lab", "all", "tok-acme-lab-abcdef0123456789")
    assert allentry["tok-acme-lab-abcdef0123456789"]["capabilities"] == "all"

    try:
        build_token_entry(
            "acme-lab", ["account", "not_a_tool"],
            "tok-invalid-cap-abcdef0123456789",
        )
        raise AssertionError("token builder should reject unknown capabilities")
    except ValueError:
        pass

    rich = build_token_entry(
        "acme--acme-sales", ["account", "knowledge_search"],
        "tok-acme-sales-abcdef0123456789",
        tenant="acme", workspace="acme-sales",
    )["tok-acme-sales-abcdef0123456789"]
    assert rich["tenant"] == "acme"
    assert rich["workspace"] == "acme-sales"
    assert rich["scope"] == "acme--acme-sales"

    derived = build_token_entry(
        "", ["account"], "tok-derived-abcdef0123456789",
        tenant="acme", workspace="sales",
    )["tok-derived-abcdef0123456789"]
    assert derived["scope"] == "acme--sales"

    try:
        build_token_entry(
            "other--sales", ["account"], "tok-mismatch-abcdef0123456789",
            tenant="acme", workspace="sales",
        )
        raise AssertionError("rich identity must not choose another physical namespace")
    except ValueError:
        pass

    # short/empty tokens refused
    for bad in ("", "short"):
        try:
            build_token_entry("acme-lab", ["chat"], bad)
            raise AssertionError("short token should have been rejected")
        except ValueError:
            pass

    print("smoke_mint_tenant_token: ALL OK")


if __name__ == "__main__":
    main()
