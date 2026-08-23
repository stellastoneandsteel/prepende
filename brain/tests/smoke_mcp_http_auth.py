#!/usr/bin/env python3
"""Smoke: MCP HTTP bearer-auth middleware (Rung-2 Step 5b — T1, T10).

Drives the pure-ASGI auth middleware through real HTTP (Starlette TestClient):
  - no / invalid bearer -> 401;
  - a valid token -> 200, and its {scope, capabilities} reaches the route via the
    context-var (the same path the MCP tool dispatch reads — proves propagation);
  - the TOKEN fixes scope: two tokens see two different scopes (isolation, anti-spoof);
  - per-token rate limit -> 429 once the window is full.

Needs Starlette (ships with `mcp`); run in the repo venv:
    .venv/bin/python3 tests/smoke_mcp_http_auth.py
Missing Starlette/TestClient is a verification failure.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from starlette.applications import Starlette
    from starlette.routing import Route
    from starlette.responses import JSONResponse
    from starlette.testclient import TestClient
except Exception as e:
    raise SystemExit(
        "smoke_mcp_http_auth: required MCP HTTP test dependency unavailable: %s"
        % type(e).__name__
    )

os.environ["PREPENDE_TENANT_TOKENS"] = (
    '{"tok-acme": "acme", '
    '"tok-globex": {"tenant": "globex", "workspace": "globex-sales", '
    '"scope": "globex--globex-sales", "capabilities": ["memory_search", "chat"]}, '
    '"tok-globex-public": {"tenant": "globex", "workspace": "globex-sales", '
    '"scope": "globex--globex-sales", "capabilities": ["account", "knowledge_search"]}, '
    '"tok-globex-private": {"tenant": "globex", "workspace": "globex-sales", '
    '"scope": "globex--globex-sales", "capabilities": '
    '["account", "knowledge_search", "memory_search", "memory_propose"]}}')
os.environ.pop("ENGRAM_TENANT_TOKENS", None)

from interface.mcp_scope import current_principal, RateLimiter  # noqa: E402
from interface.mcp_http import auth_middleware  # noqa: E402

FAILS = []


def check(name, ok, detail=""):
    print(("  ok  " if ok else "  FAIL ") + name + (("  — " + detail) if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


async def _probe(request):
    p = current_principal()
    return JSONResponse({"scope": p["scope"] if p else None,
                         "tenant": p["tenant"] if p else None,
                         "workspace": p["workspace"] if p else None,
                         "caps": sorted(p["capabilities"]) if p else None,
                         "principalId": p["principalId"] if p else None,
                         "principalFingerprint": p["principalFingerprint"] if p else None})


def main():
    print("SMOKE: MCP HTTP bearer-auth middleware (Step 5b)")
    inner = Starlette(routes=[Route("/probe", _probe)])
    client = TestClient(auth_middleware(inner))

    check("no auth -> 401", client.get("/probe").status_code == 401)
    check("invalid token -> 401",
          client.get("/probe", headers={"Authorization": "Bearer nope"}).status_code == 401)

    r = client.get("/probe", headers={"Authorization": "Bearer tok-acme"})
    body = r.json()
    check("valid token -> 200", r.status_code == 200)
    check("token's scope reaches the route via context-var", body.get("scope") == "acme",
          str(body))
    check("bare-scope token -> SAFE caps (no writes)",
          body.get("caps") and "remember" not in body["caps"] and "memory_search" in body["caps"])

    r2 = client.get("/probe", headers={"Authorization": "Bearer tok-globex"})
    check("a DIFFERENT token sees a DIFFERENT scope (token fixes scope)",
          r2.json().get("scope") == "globex--globex-sales")
    check("rich token identity reaches dispatch",
          r2.json().get("tenant") == "globex"
          and r2.json().get("workspace") == "globex-sales")
    check("explicit capabilities honored over HTTP", r2.json().get("caps") == ["chat", "memory_search"])

    public = client.get(
        "/probe", headers={"Authorization": "Bearer tok-globex-public"}
    ).json()
    private = client.get(
        "/probe", headers={"Authorization": "Bearer tok-globex-private"}
    ).json()
    check("same namespace public/private principals retain exact capabilities",
          public.get("caps") == ["account", "knowledge_search"]
          and private.get("caps") == [
              "account", "knowledge_search", "memory_propose", "memory_search",
          ])
    check("same namespace public/private principals have distinct server fingerprints",
          public.get("scope") == private.get("scope") == "globex--globex-sales"
          and public.get("principalId") != private.get("principalId")
          and public.get("principalFingerprint") != private.get("principalFingerprint")
          and "tok-globex" not in str(public)
          and "tok-globex" not in str(private))

    # principal does not leak between requests: an unauthenticated call still 401s
    check("no principal leaks across requests", client.get("/probe").status_code == 401)

    # rate limit: dedicated app, 2/min
    rl_client = TestClient(auth_middleware(inner, rate_limiter=RateLimiter(per_minute=2)))
    h = {"Authorization": "Bearer tok-acme"}
    codes = [rl_client.get("/probe", headers=h).status_code for _ in range(3)]
    check("rate limit: 2 ok then 429", codes[:2] == [200, 200] and codes[2] == 429, str(codes))

    if FAILS:
        print("\nFAILED: " + ", ".join(FAILS))
        sys.exit(1)
    print("\nPASS — HTTP bearer auth: 401/429 enforced, token fixes scope, principal reaches dispatch.")


if __name__ == "__main__":
    main()
