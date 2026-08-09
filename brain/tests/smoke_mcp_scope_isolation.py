#!/usr/bin/env python3
"""Smoke: MCP scope safety — the Rung-2 co-located-stdio contract.

Proves the three things the stdio connector path relies on (see
docs/OPENCLAW-ENGRAM-CONNECTOR.md and docs/RUNG-2-STEP-0-STDIO.md):

  S2  — no MCP tool accepts a `scope`/`tenant` parameter (an agent cannot pass a
        scope into any call; the host pins scope, the agent can't escape it).
  T15 — startup_scope_guard refuses a would-be multi-tenant deploy that forgets
        to pin ENGRAM_MCP_SCOPE (no silent bind-everyone-to-default).
  Isolation — memory written under one scope is invisible under another.

Runs on stdlib + the kernel (no `mcp` package needed):
    MODEL_PROVIDER=echo python3 tests/smoke_mcp_scope_isolation.py
"""
import ast
import asyncio
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FAILS = []


def check(name, ok, detail=""):
    print(("  ok  " if ok else "  FAIL ") + name + (("  — " + detail) if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


# --- S2: no MCP tool/resource takes a scope/tenant parameter -----------------
def test_no_scope_param():
    src = (ROOT / "interface" / "mcp_server.py").read_text()
    tree = ast.parse(src)
    banned = {"scope", "tenant", "scope_id", "tenant_id", "tenantid", "tenant_scope"}
    tools, offenders = 0, []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        is_tool = False
        for dec in node.decorator_list:
            # matches @mcp.tool(), @mcp.resource(...), @mcp.tool
            target = dec.func if isinstance(dec, ast.Call) else dec
            if isinstance(target, ast.Attribute) and target.attr in ("tool", "resource"):
                is_tool = True
            if isinstance(target, ast.Name) and target.id in (
                "_capability_tool", "_capability_resource"
            ):
                is_tool = True
        if not is_tool:
            continue
        tools += 1
        a = node.args
        names = [arg.arg for arg in (a.posonlyargs + a.args + a.kwonlyargs)]
        bad = [n for n in names if n.lower() in banned]
        if bad:
            offenders.append("%s(%s)" % (node.name, ",".join(bad)))
    check("S2: at least one MCP tool found to scan", tools >= 8, "found %d" % tools)
    check("S2: no MCP tool takes a scope/tenant param", not offenders,
          "offenders: " + ", ".join(offenders))


# --- T15: startup guard refuses silent default-scope binding ------------------
def test_startup_guard():
    from interface.mcp_scope import startup_scope_guard
    # safe: nothing configured
    check("T15: unconfigured boots", startup_scope_guard({}) is None)
    # safe: single non-default scope but explicitly pinned
    pinned = {"ENGRAM_TENANT_TOKENS": '{"tok-a": "acme"}', "ENGRAM_MCP_SCOPE": "acme"}
    check("T15: pinned scope boots", startup_scope_guard(pinned) is None)
    prepende_pinned = {
        "PREPENDE_TENANT_TOKENS": '{"tok-a": "acme"}',
        "PREPENDE_MCP_SCOPE": "acme",
    }
    check("T15: Prepende-prefixed scope boots", startup_scope_guard(prepende_pinned) is None)
    bad_pin = {"PREPENDE_MCP_SCOPE": "../other-tenant"}
    check("T15: invalid stdio scope pin is REFUSED",
          isinstance(startup_scope_guard(bad_pin), str))
    rich_pinned = {
        "PREPENDE_TENANT_TOKENS": (
            '{"tok-a":{"tenant":"acme","workspace":"acme-sales",'
            '"scope":"acme--acme-sales","capabilities":["account"]}}'
        ),
        "PREPENDE_MCP_SCOPE": "acme--acme-sales",
    }
    check("T15: rich identity token map boots when pinned",
          startup_scope_guard(rich_pinned) is None)
    # safe: only the default scope declared, unpinned
    only_default = {"ENGRAM_TENANT_TOKENS": '{"tok-a": "default"}'}
    check("T15: default-only unpinned boots", startup_scope_guard(only_default) is None)
    # UNSAFE: non-default scopes declared but no pin -> must refuse
    unsafe = {"ENGRAM_TENANT_TOKENS": '{"tok-a": "acme", "tok-b": "globex"}'}
    msg = startup_scope_guard(unsafe)
    check("T15: multi-tenant w/o pin is REFUSED", isinstance(msg, str) and "ENGRAM_MCP_SCOPE" in msg)
    rich_unsafe = {
        "PREPENDE_TENANT_TOKENS": (
            '{"tok-a":{"tenant":"acme","workspace":"acme-sales",'
            '"scope":"acme--acme-sales"}}'
        ),
    }
    check("T15: rich identity w/o stdio pin is REFUSED",
          isinstance(startup_scope_guard(rich_unsafe), str))
    rich_http = {**rich_unsafe, "PREPENDE_MCP_TRANSPORT": "http"}
    check("T15: rich HTTP identity needs no process-wide scope pin",
          startup_scope_guard(rich_http) is None)
    partial = {
        "PREPENDE_TENANT_TOKENS": (
            '{"tok-a":{"tenant":"acme","scope":"acme-sales"}}'
        ),
        "PREPENDE_MCP_SCOPE": "acme--acme-sales",
    }
    check("T15: partial rich identity is REFUSED",
          isinstance(startup_scope_guard(partial), str))
    mismatched_namespace = {
        "PREPENDE_MCP_TRANSPORT": "http",
        "PREPENDE_TENANT_TOKENS": (
            '{"tok-a":{"tenant":"acme","workspace":"sales",'
            '"scope":"another-tenant--sales"}}'
        ),
    }
    check("T15: rich identity cannot select another namespace",
          isinstance(startup_scope_guard(mismatched_namespace), str))
    process_mismatch = {
        "PREPENDE_MCP_TENANT": "acme",
        "PREPENDE_MCP_WORKSPACE": "sales",
        "PREPENDE_MCP_SCOPE": "other--sales",
    }
    check("T15: stdio process identity cannot select another namespace",
          isinstance(startup_scope_guard(process_mismatch), str))
    check("T15: invalid deployment revision is REFUSED",
          isinstance(startup_scope_guard({"PREPENDE_DEPLOYMENT_REVISION": "../bad"}), str))
    # malformed json -> refuse with a clear message
    bad = {"ENGRAM_TENANT_TOKENS": "{not json"}
    check("T15: malformed token map is REFUSED", isinstance(startup_scope_guard(bad), str))
    bad_http = {**bad, "PREPENDE_MCP_TRANSPORT": "http"}
    check("T15: malformed HTTP token map is still REFUSED",
          isinstance(startup_scope_guard(bad_http), str))
    non_object_http = {
        "PREPENDE_MCP_TRANSPORT": "http",
        "PREPENDE_TENANT_TOKENS": '["acme"]',
    }
    check("T15: non-object HTTP token map is REFUSED",
          isinstance(startup_scope_guard(non_object_http), str))
    empty_token_http = {
        "PREPENDE_MCP_TRANSPORT": "http",
        "PREPENDE_TENANT_TOKENS": '{"": "acme"}',
    }
    check("T15: empty HTTP token identity is REFUSED",
          isinstance(startup_scope_guard(empty_token_http), str))


# --- Isolation: memory is scope-filtered -------------------------------------
def test_memory_isolation():
    tmp = tempfile.mkdtemp(prefix="engram-smoke-")
    os.environ.update({
        "MODEL_PROVIDER": "echo", "MEMORY_BACKEND": "sqlite",
        "MEMORY_DB": tmp + "/memory.db", "RUNS_DB": tmp + "/runs.db",
        "KNOWLEDGE_DB": tmp + "/knowledge.db", "VAULT_PATH": tmp + "/vault",
    })
    from kernel.core.brain import build_brain
    loop, _cfg, _gw = build_brain()
    if loop.memory is None:
        check("isolation: memory store available", False, "loop.memory is None")
        return

    async def run():
        await loop.memory.write("the launch code is sunflower-meridian", scope="tenant-a",
                                metadata={"kind": "semantic"})
        a = await loop.memory.search("launch code sunflower", scope="tenant-a", k=5)
        b = await loop.memory.search("launch code sunflower", scope="tenant-b", k=5)
        return a, b

    a_hits, b_hits = asyncio.run(run())
    check("isolation: owner scope recalls its fact", len(a_hits) >= 1)
    check("isolation: other scope sees NOTHING (no cross-tenant leak)", len(b_hits) == 0,
          "tenant-b saw %d hits" % len(b_hits))


def main():
    print("SMOKE: MCP scope isolation (Rung-2 stdio contract)")
    test_no_scope_param()
    test_startup_guard()
    test_memory_isolation()
    if FAILS:
        print("\nFAILED: " + ", ".join(FAILS))
        sys.exit(1)
    print("\nPASS — scope is host-pinned, unspoofable, and isolated.")


if __name__ == "__main__":
    main()
