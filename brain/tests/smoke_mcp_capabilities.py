#!/usr/bin/env python3
"""Smoke: MCP capability scoping (Rung-2 Step 4 — T2 least privilege).

Proves:
  - ENGRAM_MCP_CAPABILITIES parses correctly (unset=ALL, untrusted=SAFE, all=ALL, list);
  - an untrusted (SAFE) connection is denied every write/action tool;
  - every MCP tool is registered through the mandatory dispatch wrapper;
  - a four-capability principal is denied every other registered tool.

Runs on stdlib (no `mcp` package needed):
    MODEL_PROVIDER=echo python3 tests/smoke_mcp_capabilities.py
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FAILS = []


def check(name, ok, detail=""):
    print(("  ok  " if ok else "  FAIL ") + name + (("  — " + detail) if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


def test_logic():
    from interface.mcp_scope import allowed_capabilities, is_allowed, SAFE_TOOLS, WRITE_TOOLS, ALL_TOOLS
    check("unset -> ALL_TOOLS (operator default)", allowed_capabilities({}) == set(ALL_TOOLS))
    check("'untrusted' -> SAFE_TOOLS", allowed_capabilities({"ENGRAM_MCP_CAPABILITIES": "untrusted"}) == set(SAFE_TOOLS))
    check("Prepende env -> SAFE_TOOLS", allowed_capabilities({"PREPENDE_MCP_CAPABILITIES": "safe"}) == set(SAFE_TOOLS))
    check("Prepende env wins over legacy alias", allowed_capabilities({
        "PREPENDE_MCP_CAPABILITIES": "safe",
        "ENGRAM_MCP_CAPABILITIES": "all",
    }) == set(SAFE_TOOLS))
    check("'all' -> ALL_TOOLS", allowed_capabilities({"ENGRAM_MCP_CAPABILITIES": "all"}) == set(ALL_TOOLS))
    check("comma-list -> exactly those",
          allowed_capabilities({"ENGRAM_MCP_CAPABILITIES": "memory_search, chat"}) == {"memory_search", "chat"})
    check("unknown/removed capability names grant nothing",
          allowed_capabilities({
              "PREPENDE_MCP_CAPABILITIES": "memory_approve,ingest_knowledge"
          }) == set())
    safe = {"ENGRAM_MCP_CAPABILITIES": "untrusted"}
    for t in WRITE_TOOLS:
        check("untrusted DENIED write/action tool: %s" % t, not is_allowed(t, safe))
    for t in SAFE_TOOLS:
        check("untrusted ALLOWED safe tool: %s" % t, is_allowed(t, safe))
    # SAFE and WRITE are disjoint and cover ALL
    check("SAFE/WRITE disjoint + cover ALL",
          not (SAFE_TOOLS & WRITE_TOOLS) and (SAFE_TOOLS | WRITE_TOOLS) == ALL_TOOLS)
    check("approval/import are not MCP capabilities",
          "memory_approve" not in ALL_TOOLS and "ingest_knowledge" not in ALL_TOOLS)


def test_guard_present():
    """Every tool must use the one mechanical dispatch-time wrapper."""
    from interface import mcp_scope
    src = (ROOT / "interface" / "mcp_server.py").read_text()
    tree = ast.parse(src)
    tools: set[str] = set()
    unguarded: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        decorators = [d.func if isinstance(d, ast.Call) else d for d in node.decorator_list]
        if any(isinstance(d, ast.Name) and d.id == "_capability_tool" for d in decorators):
            tools.add(node.name)
        elif any(
            isinstance(d, ast.Attribute) and d.attr == "tool" for d in decorators
        ):
            unguarded.add(node.name)
    check("no MCP tool bypasses the dispatch wrapper", not unguarded,
          "unguarded: " + ", ".join(sorted(unguarded)))
    check("dispatch wrapper covers the full declared tool set", tools == set(mcp_scope.ALL_TOOLS),
          f"wrapped={sorted(tools)} declared={sorted(mcp_scope.ALL_TOOLS)}")

    four = {"account", "chat", "memory_search", "knowledge_search"}
    context = mcp_scope.set_principal({
        "tenant": "tenant-a",
        "workspace": "workspace-a",
        "scope": "tenant-a--workspace-a",
        "capabilities": four,
    })
    try:
        denied = {tool for tool in tools if not mcp_scope.is_allowed(tool)}
    finally:
        mcp_scope.reset_principal(context)
    check("four-cap token is denied every other registered tool",
          denied == tools - four,
          f"denied={sorted(denied)} expected={sorted(tools - four)}")


def main():
    print("SMOKE: MCP capability scoping (Step 4)")
    test_logic()
    test_guard_present()
    if FAILS:
        print("\nFAILED: " + ", ".join(FAILS))
        sys.exit(1)
    print("\nPASS — least privilege enforced; every MCP tool is dispatch-gated.")


if __name__ == "__main__":
    main()
