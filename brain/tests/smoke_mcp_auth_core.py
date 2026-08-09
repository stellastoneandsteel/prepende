#!/usr/bin/env python3
"""Smoke: MCP per-call auth CORE (Rung-2 Step 5a — T1, T8, T10).

The verifiable, transport-independent half of HTTP auth:
  - token_to_principal maps an opaque token to a validated
    {tenant, workspace, scope, capabilities} principal (least-privilege
    default), and an unknown/empty token resolves to None (-> 401 at the edge);
  - a request-scoped PRINCIPAL overrides env for is_allowed (per-token capabilities);
  - the RateLimiter enforces N/60s with an injectable clock (T10);
  - run_workflow_async REJECTS caller-supplied gate keys mode/requiresApproval (T8).

(The Starlette ASGI middleware that sets the principal from the Authorization header
is Step 5b, verified via TestClient.) Runs on stdlib:
    MODEL_PROVIDER=echo python3 tests/smoke_mcp_auth_core.py
"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FAILS = []


def check(name, ok, detail=""):
    print(("  ok  " if ok else "  FAIL ") + name + (("  — " + detail) if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


def test_principal():
    from interface import mcp_scope as ms
    env = {"ENGRAM_TENANT_TOKENS": (
        '{"tok-bare": "acme", '
        '"tok-rich": {"scope": "globex", "capabilities": ["memory_search", "chat"]}, '
        '"tok-identity": {"tenant": "initech", "workspace": "initech-sales", '
        '"scope": "initech--initech-sales", "capabilities": ["account", "knowledge_search"]}, '
        '"tok-identity-private": {"tenant": "initech", "workspace": "initech-sales", '
        '"scope": "initech--initech-sales", "capabilities": ["account", "knowledge_search", "memory_search", "memory_propose"]}, '
        '"tok-mismatch": {"tenant": "initech", "workspace": "initech-sales", '
        '"scope": "other-namespace", "capabilities": ["account"]}, '
        '"tok-full": {"scope": "umbrella", "capabilities": "all"}, '
        '"tok-partial": {"tenant": "broken", "scope": "broken-sales"}, '
        '"tok-unknown-cap": {"scope": "broken", "capabilities": ["account", "not_a_tool"]}, '
        '"tok-numeric": {"scope": 123, "capabilities": ["account"]}}')}
    bare = ms.token_to_principal("tok-bare", env)
    check("bare-scope token -> SAFE caps (least privilege)",
          bare and bare["scope"] == "acme" and bare["tenant"] == "acme"
          and bare["workspace"] == "acme" and bare["capabilities"] == set(ms.SAFE_TOOLS))
    rich = ms.token_to_principal("tok-rich", env)
    check("legacy rich scope defaults tenant/workspace",
          rich and rich["tenant"] == "globex" and rich["workspace"] == "globex")
    check("explicit capabilities honored",
          rich and rich["capabilities"] == {"memory_search", "chat"})
    identity = ms.token_to_principal("tok-identity", env)
    check("rich identity preserves tenant + workspace + physical scope",
          identity and identity["tenant"] == "initech"
          and identity["workspace"] == "initech-sales"
          and identity["scope"] == "initech--initech-sales")
    private_identity = ms.token_to_principal("tok-identity-private", env)
    check("token principal carries only a one-way server fingerprint",
          identity and identity["principalId"].startswith("mcp-token:sha256:")
          and identity["principalFingerprint"].startswith("sha256:")
          and "tok-identity" not in identity["principalId"]
          and "tok-identity" not in identity["principalFingerprint"])
    check("same namespace, different tokens -> distinct principal fingerprints",
          identity and private_identity
          and identity["scope"] == private_identity["scope"]
          and identity["principalId"] != private_identity["principalId"]
          and identity["principalFingerprint"] != private_identity["principalFingerprint"])
    check("rich identity cannot select another physical namespace",
          ms.token_to_principal("tok-mismatch", env) is None)
    full = ms.token_to_principal("tok-full", env)
    check("'all' capabilities -> ALL_TOOLS", full and full["capabilities"] == set(ms.ALL_TOOLS))
    check("partial rich identity fails closed", ms.token_to_principal("tok-partial", env) is None)
    check("unknown capability fails closed", ms.token_to_principal("tok-unknown-cap", env) is None)
    check("non-string identity fails closed", ms.token_to_principal("tok-numeric", env) is None)
    check("unknown token -> None (=> 401)", ms.token_to_principal("nope", env) is None)
    check("empty token -> None", ms.token_to_principal("", env) is None)
    check("no token map -> None", ms.token_to_principal("x", {}) is None)
    invalid_scope_env = {"PREPENDE_TENANT_TOKENS": '{"tok": "../other-tenant"}'}
    check("invalid token scope -> None (=> 401)",
          ms.token_to_principal("tok", invalid_scope_env) is None)


def test_principal_overrides_caps():
    from interface import mcp_scope as ms
    # with a SAFE principal set, writes are denied / reads allowed — regardless of env
    tok = ms.set_principal({"scope": "acme", "capabilities": set(ms.SAFE_TOOLS)})
    try:
        check("principal: write tool denied", not ms.is_allowed("remember"))
        check("principal: safe tool allowed", ms.is_allowed("memory_propose"))
    finally:
        ms.reset_principal(tok)
    check("no principal -> falls back to env (default ALL)", ms.is_allowed("remember", {}))
    check("candidate approval is not an MCP capability",
          "memory_approve" not in ms.ALL_TOOLS
          and not ms.is_allowed("memory_approve", {"PREPENDE_MCP_CAPABILITIES": "all"})
          and not ms.is_allowed("memory_approve", {
              "PREPENDE_MCP_CAPABILITIES": "memory_approve"
          }))
    check("knowledge import is not an MCP capability",
          "ingest_knowledge" not in ms.ALL_TOOLS
          and not ms.is_allowed("ingest_knowledge", {"PREPENDE_MCP_CAPABILITIES": "all"})
          and not ms.is_allowed("ingest_knowledge", {
              "PREPENDE_MCP_CAPABILITIES": "ingest_knowledge"
          }))


def test_rate_limiter():
    from interface.mcp_scope import RateLimiter
    rl = RateLimiter(per_minute=3)
    now = 1000.0
    check("first 3 allowed", all(rl.allow("k", now) for _ in range(3)))
    check("4th blocked in window", not rl.allow("k", now + 1))
    check("allowed again after window slides", rl.allow("k", now + 61))
    check("per_minute<=0 disables limiting", RateLimiter(per_minute=0).allow("k", now))


def test_rich_map_consumers():
    import os
    from interface import mcp_scope as ms

    keys = ("PREPENDE_TENANT_TOKENS", "ENGRAM_TENANT_TOKENS")
    saved = {key: os.environ.get(key) for key in keys}
    try:
        os.environ["PREPENDE_TENANT_TOKENS"] = (
            '{"tok":{"tenant":"acme","workspace":"acme-sales",'
            '"scope":"acme--acme-sales","capabilities":["account"]},'
            '"invalid":{"scope":"must-not-authorize",'
            '"capabilities":["not_a_tool"]}}'
        )
        os.environ.pop("ENGRAM_TENANT_TOKENS", None)
        check("configured consumer reads rich token scopes",
              ms.configured_physical_scopes("owner")
              == ["acme--acme-sales", "owner"])

        os.environ["PREPENDE_TENANT_TOKENS"] = "   "
        os.environ["ENGRAM_TENANT_TOKENS"] = '{"legacy-token":"legacy-scope"}'
        check("MCP token resolver uses the same whitespace-safe fallback",
              ms.token_to_principal("legacy-token", os.environ)["scope"]
              == "legacy-scope")
        check("configured consumer uses the same whitespace-safe fallback",
              ms.configured_physical_scopes("owner")
              == ["legacy-scope", "owner"])
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def test_t8_gatekey_rejection():
    import os
    os.environ["MODEL_PROVIDER"] = "echo"
    from interface import prepende_runtime as v1
    st, obj = asyncio.run(v1.run_workflow_async("t8-scope", {"workflow": "x", "params": {"mode": "live"}}))
    check("run_workflow rejects caller mode=live (400)", st == 400 and "gate keys" in obj.get("error", ""))
    st2, obj2 = asyncio.run(v1.run_workflow_async("t8-scope", {"workflow": "x", "params": {"requiresApproval": False}}))
    check("run_workflow rejects caller requiresApproval=false (400)", st2 == 400)


def test_mcp_workflow_stages_without_webhook_execution():
    import os
    import tempfile

    os.environ["MODEL_PROVIDER"] = "echo"
    from interface import prepende_runtime as v1

    saved_workflows = os.environ.get("PREPENDE_WORKFLOWS")
    saved_prepende_approvals = os.environ.get("PREPENDE_APPROVALS_DB")
    saved_engram_approvals = os.environ.get("ENGRAM_APPROVALS_DB")
    try:
        os.environ["PREPENDE_WORKFLOWS"] = (
            '{"workflows":[{"name":"founder_absence_audit",'
            '"description":"prepare a read-only founder absence audit",'
            '"url":"https://n8n.invalid/webhook/absence-audit"}]}'
        )
        with tempfile.TemporaryDirectory() as temporary:
            os.environ["PREPENDE_APPROVALS_DB"] = str(Path(temporary) / "approvals.db")
            os.environ.pop("ENGRAM_APPROVALS_DB", None)
            v1._loop = None
            v1._cfg = None
            v1._gw = None
            v1._approvals = None
            status, body = asyncio.run(v1.run_workflow_async(
                "test-only-scope",
                {
                    "workflow": "founder_absence_audit",
                    "goal": "prepare the read-only absence audit",
                    "params": {"days": 7},
                    "requestedBy": "mcp-smoke",
                },
            ))
            check(
                "MCP workflow stage returns an approval receipt without execution",
                status == 200
                and body.get("ok") is True
                and body.get("mode") == "dry_run"
                and body.get("approvalRequired") is True
                and body.get("actionExecuted") is False
                and body.get("externalActions") == "none"
                and body.get("receipt", {}).get("approvalState") == "required"
                and body.get("receipt", {}).get("actionExecuted") is False,
                str(body),
            )
    finally:
        v1._loop = None
        v1._cfg = None
        v1._gw = None
        v1._approvals = None
        if saved_workflows is None:
            os.environ.pop("PREPENDE_WORKFLOWS", None)
        else:
            os.environ["PREPENDE_WORKFLOWS"] = saved_workflows
        if saved_prepende_approvals is None:
            os.environ.pop("PREPENDE_APPROVALS_DB", None)
        else:
            os.environ["PREPENDE_APPROVALS_DB"] = saved_prepende_approvals
        if saved_engram_approvals is None:
            os.environ.pop("ENGRAM_APPROVALS_DB", None)
        else:
            os.environ["ENGRAM_APPROVALS_DB"] = saved_engram_approvals


def main():
    print("SMOKE: MCP per-call auth core (Step 5a)")
    test_principal()
    test_principal_overrides_caps()
    test_rate_limiter()
    test_rich_map_consumers()
    test_t8_gatekey_rejection()
    test_mcp_workflow_stages_without_webhook_execution()
    if FAILS:
        print("\nFAILED: " + ", ".join(FAILS))
        sys.exit(1)
    print("\nPASS — token->principal, per-token caps, rate limiting, and T8 gate-key rejection.")


if __name__ == "__main__":
    main()
