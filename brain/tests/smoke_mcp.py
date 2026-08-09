"""Smoke: the inbound MCP surface honors the parity contract.

Proves (direct tool invocation, echo provider, zero infra):
  1. the tool set matches the parity contract
  2. chat routes like /v1/chat: fast chat doesn't loop; external actions are
     approval-gated with actionExecuted false
  3. pursue_goal returns the truthful run receipt (memory candidate-gated)
  4. memory_propose stages WITHOUT writing; approval/import are absent from
     MCP; remember (explicit user statement) is the sole durable writer
  5. account is redacted and reports the gates

The `mcp` package needs python >= 3.10; on python 3.9 this re-execs itself
under the repo .venv if present (else fails with the install hint).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

try:
    import mcp  # noqa: F401
except ImportError:
    venv_py = os.path.join(ROOT, ".venv", "bin", "python3")
    if os.path.exists(venv_py) and os.environ.get("ENGRAM_MCP_REEXEC") != "1":
        os.environ["ENGRAM_MCP_REEXEC"] = "1"
        os.execv(venv_py, [venv_py, os.path.abspath(__file__)])
    raise SystemExit("mcp package missing: python3.10+ and `.venv/bin/pip install mcp` required")

# Isolate + force echo + deterministic storage, before importing the MCP surface.
_tmp = tempfile.mkdtemp(prefix="engram_mcp_")
os.environ["MODEL_PROVIDER"] = "echo"
os.environ["WORKSPACE_ROOT"] = os.path.join(_tmp, "ws")
os.environ["MEMORY_DB"] = os.path.join(_tmp, "memory.db")
os.environ["MEMORY_BACKEND"] = "sqlite"  # hermetic: never inherit a machine DATABASE_URL
os.environ["RUNS_DB"] = os.path.join(_tmp, "runs.db")
os.environ["VAULT_PATH"] = os.path.join(_tmp, "vault")
os.environ["PREPENDE_MCP_TENANT"] = "steel-buildings"
os.environ["PREPENDE_MCP_WORKSPACE"] = "steel-buildings-sales"
os.environ["PREPENDE_MCP_SCOPE"] = "steel-buildings--steel-buildings-sales"
os.environ["PREPENDE_MCP_CAPABILITIES"] = "all"
os.environ["PREPENDE_DEPLOYMENT_REVISION"] = "release-smoke-1"
for key in (
    "ENGRAM_MCP_TENANT", "ENGRAM_MCP_WORKSPACE", "ENGRAM_MCP_SCOPE",
    "ENGRAM_MCP_CAPABILITIES", "PREPENDE_TENANT_TOKENS", "ENGRAM_TENANT_TOKENS",
):
    os.environ.pop(key, None)

from interface import mcp_server  # noqa: E402


async def main() -> None:
    tools = await mcp_server.mcp.list_tools()
    names = {t.name for t in tools}
    expected = {"chat", "pursue_goal", "memory_search", "remember", "memory_propose",
                "memory_candidates", "memory_reject",
                "account", "list_workflows", "run_workflow",
                "knowledge_search", "knowledge_related"}
    assert names == expected, names
    assert not hasattr(mcp_server, "memory_approve"), "approval must not be an MCP function"
    assert not hasattr(mcp_server, "ingest_knowledge"), "import must not be an MCP function"
    print(f"OK tools: {sorted(names)}")

    # A token with exactly four capabilities must receive a mechanical 403 from
    # every other registered tool before any tool body can run.
    from interface import mcp_scope
    four_caps = {"account", "chat", "memory_search", "knowledge_search"}
    denied_calls = {
        "pursue_goal": lambda: mcp_server.pursue_goal("plan a harmless fixture"),
        "remember": lambda: mcp_server.remember("a long enough fixture fact"),
        "memory_propose": lambda: mcp_server.memory_propose("a harmless proposed fact"),
        "memory_candidates": lambda: mcp_server.memory_candidates(),
        "memory_reject": lambda: mcp_server.memory_reject("fixture-id"),
        "list_workflows": lambda: mcp_server.list_workflows(),
        "run_workflow": lambda: mcp_server.run_workflow(workflow="fixture"),
        "knowledge_related": lambda: mcp_server.knowledge_related("fixture-page"),
    }
    assert set(denied_calls) == names - four_caps, (set(denied_calls), names - four_caps)
    principal_token = mcp_scope.set_principal({
        "tenant": "steel-buildings",
        "workspace": "steel-buildings-sales",
        "scope": "steel-buildings--steel-buildings-sales",
        "capabilities": four_caps,
    })
    try:
        for tool_name, invoke in denied_calls.items():
            denied = await invoke()
            assert isinstance(denied, dict), (tool_name, denied)
            assert denied.get("httpStatus") == 403 and denied.get("capability") == tool_name, (
                tool_name, denied,
            )
    finally:
        mcp_scope.reset_principal(principal_token)
    print("OK capabilities: four-cap token denied by every other tool at dispatch")

    # 2. Routing parity.
    fast = await mcp_server.chat("hi there")
    assert fast["loop"]["used"] is False and fast["loop"]["mode"] == "fast_chat", fast["loop"]
    gated = await mcp_server.chat("send the invoice to the client now")
    assert gated["approvalRequired"] is True and gated["actionExecuted"] is False, gated
    assert gated["loop"]["mode"] == "approval_required", gated["loop"]
    print("OK chat: fast path no loop; external action approval-gated, not executed")

    # 3. Goal receipt with candidate-gated memory.
    r = await mcp_server.pursue_goal("Say hello in one short sentence.")
    assert r["answer"].strip(), r
    receipt = r["receipt"]
    assert receipt["mode"] == "goal_loop" and receipt["actionExecuted"] is False, receipt
    assert receipt["memory"]["written"] == [], receipt["memory"]
    print("OK pursue_goal: run receipt present, memory proposed-not-written")

    # 4. Propose stages nothing; remember writes; search is scoped truth.
    staged = await mcp_server.memory_propose("the warehouse is probably in Vergennes")
    assert staged["persisted"] is False and staged["durableWrite"] is False, staged
    assert staged["provenance"]["principalId"].startswith("mcp-stdio:sha256:"), staged
    assert staged["provenance"]["principalFingerprint"].startswith("sha256:"), staged
    assert staged["provenance"]["connector"] == "mcp_stdio", staged
    assert staged["provenance"]["approvalPath"] == "owner_approval_outside_mcp", staged
    cand_id = staged["candidate"]["id"]
    assert cand_id.startswith("cand_"), staged
    found = await mcp_server.memory_search("warehouse Vergennes")
    assert all("Vergennes" not in h["content"] for h in found["hits"]), found

    # 4b. Candidates remain pending because promotion is owner-side, not MCP.
    queue = await mcp_server.memory_candidates()
    assert any(c["id"] == cand_id for c in queue["pending"]), queue
    found = await mcp_server.memory_search("warehouse Vergennes")
    assert all("Vergennes" not in h["content"] for h in found["hits"]), found
    print("OK owner boundary: candidate stays pending; approval/import absent from MCP")
    written = await mcp_server.remember("my company is Northwind Fabrication", kind="profile-bad-kind")
    assert written["persisted"] is True and written["written"]["kind"] == "semantic", written
    found = await mcp_server.memory_search("Northwind Fabrication company")
    assert found["count"] >= 1 and any("Northwind" in h["content"] for h in found["hits"]), found
    print("OK memory gates: propose stages only; explicit remember writes; kinds clamped")

    # 5. Account receipt.
    acct = await mcp_server.account()
    assert acct["tenant"] == "steel-buildings", acct
    assert acct["tenantId"] == "steel-buildings", acct
    assert acct["workspace"] == "steel-buildings-sales", acct
    assert acct["workspaceId"] == "steel-buildings-sales", acct
    assert acct["scope"] == "steel-buildings--steel-buildings-sales", acct
    assert acct["deploymentRevision"] == "release-smoke-1", acct
    assert acct["deploymentRevisionConfigured"] is True, acct
    assert acct["capabilities"] == sorted(expected), acct
    assert acct["principalId"].startswith("mcp-stdio:sha256:"), acct
    assert acct["principalFingerprint"].startswith("sha256:"), acct
    assert acct["memoryPolicy"] == "candidate", acct
    assert acct["externalActions"] == "approval_required", acct
    print("OK account: stdio principal + exact capabilities + gates, redacted")

    # Public and private bearer principals may share one tenant/workspace, but
    # their token-derived receipts and exact capability arrays must be distinct.
    public_token = "tok-public-fixture-0123456789abcdef"
    private_token = "tok-private-fixture-0123456789abcdef"
    token_env = {"PREPENDE_TENANT_TOKENS": json.dumps({
        public_token: {
            "tenant": "steel-buildings",
            "workspace": "steel-buildings-sales",
            "scope": "steel-buildings--steel-buildings-sales",
            "capabilities": ["account", "knowledge_search"],
        },
        private_token: {
            "tenant": "steel-buildings",
            "workspace": "steel-buildings-sales",
            "scope": "steel-buildings--steel-buildings-sales",
            "capabilities": [
                "account", "knowledge_search", "memory_search", "memory_propose",
            ],
        },
    })}
    bearer_accounts = []
    for raw_token in (public_token, private_token):
        principal = mcp_scope.token_to_principal(raw_token, token_env)
        assert principal is not None, raw_token
        marker = mcp_scope.set_principal(principal)
        try:
            bearer_accounts.append(await mcp_server.account())
        finally:
            mcp_scope.reset_principal(marker)
    public_account, private_account = bearer_accounts
    assert public_account["capabilities"] == ["account", "knowledge_search"], public_account
    assert private_account["capabilities"] == [
        "account", "knowledge_search", "memory_propose", "memory_search",
    ], private_account
    assert public_account["tenant"] == private_account["tenant"]
    assert public_account["workspace"] == private_account["workspace"]
    assert public_account["scope"] == private_account["scope"]
    assert public_account["principalId"] != private_account["principalId"]
    assert public_account["principalFingerprint"] != private_account["principalFingerprint"]
    serialized_accounts = json.dumps(bearer_accounts, sort_keys=True)
    assert public_token not in serialized_accounts and private_token not in serialized_accounts
    print("OK account: public/private bearer principals are distinguishable without token leakage")

    # Canonical values win when both names are configured. The legacy names
    # remain a compatibility fallback only when the canonical names are absent.
    keys = (
        "PREPENDE_MCP_TENANT", "PREPENDE_MCP_WORKSPACE", "PREPENDE_MCP_SCOPE",
        "PREPENDE_MCP_TRANSPORT", "ENGRAM_MCP_TENANT", "ENGRAM_MCP_WORKSPACE",
        "ENGRAM_MCP_SCOPE", "ENGRAM_MCP_TRANSPORT",
    )
    saved = {key: os.environ.get(key) for key in keys}
    try:
        os.environ.update({
            "PREPENDE_MCP_TENANT": "canonical-steel",
            "PREPENDE_MCP_WORKSPACE": "canonical-steel-sales",
            "PREPENDE_MCP_SCOPE": "canonical-steel--canonical-steel-sales",
            "PREPENDE_MCP_TRANSPORT": "stdio",
            "ENGRAM_MCP_TENANT": "legacy-steel",
            "ENGRAM_MCP_WORKSPACE": "legacy-steel-sales",
            "ENGRAM_MCP_SCOPE": "legacy-steel--legacy-steel-sales",
            "ENGRAM_MCP_TRANSPORT": "http",
        })
        assert mcp_server._identity() == {
            "tenant": "canonical-steel",
            "workspace": "canonical-steel-sales",
            "scope": "canonical-steel--canonical-steel-sales",
        }
        assert mcp_server._transport() == "stdio"
        for key in (
            "PREPENDE_MCP_TENANT", "PREPENDE_MCP_WORKSPACE",
            "PREPENDE_MCP_SCOPE", "PREPENDE_MCP_TRANSPORT",
        ):
            os.environ.pop(key, None)
        assert mcp_server._identity() == {
            "tenant": "legacy-steel",
            "workspace": "legacy-steel-sales",
            "scope": "legacy-steel--legacy-steel-sales",
        }
        assert mcp_server._transport() == "http"
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    print("OK stdio identity: canonical precedence and legacy fallback agree")

    wfs = await mcp_server.list_workflows()
    assert isinstance(wfs, list), wfs

    print("\nMCP SMOKE (parity contract): OK")


if __name__ == "__main__":
    asyncio.run(main())
