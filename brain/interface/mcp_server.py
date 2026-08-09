"""Inbound MCP server for Prepende — the agent-side cockpit.

This adapter uses the product-neutral runtime contract in
``interface/prepende_runtime.py``. Product-specific HTTP APIs may compose the
same kernel without entering a clean Prepende distribution.

- chat            -> fast_chat / goal_loop / approval_required + full receipt
- pursue_goal     -> GoalLoop with run receipt (memory candidate-gated)
- memory_search   -> scoped recall (id, kind, content)
- remember        -> durable write ONLY as a relayed explicit user statement
- memory_propose  -> stages a candidate into the durable review queue; writes NO memory
- memory_candidates / memory_reject -> review-queue visibility and rejection only
- run_workflow    -> dry_run + requiresApproval enforced; actionExecuted honest
- account         -> redacted identity receipt

Candidate approval and knowledge import are deliberately absent from MCP. An
owner must use the separately authenticated approval surface or the reviewed
knowledge-bundle CLI. This keeps ``remember`` as MCP's only durable-memory
write path.

Tenant scope: PREPENDE_MCP_SCOPE (env), with ENGRAM_MCP_SCOPE accepted during
the compatibility window, else the config memory scope. Tokens and secrets
never transit this surface. Requires `mcp` (python >= 3.10): use the repo
.venv —  .venv/bin/python3 -m interface.mcp_server
"""

from __future__ import annotations

import hashlib
import functools
import json
import os
from typing import Any

from mcp.server.fastmcp import FastMCP

from interface import prepende_runtime as runtime
from interface.mcp_scope import (
    ALL_TOOLS,
    current_principal,
    deployment_revision,
    is_allowed,
    startup_scope_guard,
)
from kernel.core.intake import scan_intake
from knowledge.scoped import ScopedVaults, validate_scope
from memory.candidates import default_queue
from prepende_brain.identity import require_identity_namespace
from prepende_brain.private_fs import enforce_private_umask

# Importing this module is the earliest common entry for both stdio and HTTP.
# Reassert in main as well in case an embedding host changed its process umask.
enforce_private_umask()

mcp = FastMCP("prepende")

_MEMORY_KINDS = ("episodic", "semantic", "procedural")

_scoped_vaults: ScopedVaults | None = None


def _mcp_env(suffix: str) -> str:
    """Read a canonical MCP setting, treating whitespace as unset."""

    canonical = os.environ.get(f"PREPENDE_MCP_{suffix}", "").strip()
    if canonical:
        return canonical
    return os.environ.get(f"ENGRAM_MCP_{suffix}", "").strip()


def _vaults() -> ScopedVaults:
    """Per-tenant vault namespaces (threat: cross-tenant knowledge exposure).
    The default scope keeps the operator's vault instance; every other scope
    gets its own vault tree + RAG index under <vault>/tenants/<scope>/."""
    global _scoped_vaults
    if _scoped_vaults is None:
        loop = runtime._brain()
        _scoped_vaults = getattr(loop, "scoped_vaults", None)
        if _scoped_vaults is None:
            _scoped_vaults = ScopedVaults(
                base_path=getattr(runtime._cfg, "vault", "./vault"),
                default_scope=getattr(runtime._cfg, "memory_scope", "default") or "default",
                default_knowledge=getattr(loop, "knowledge", None),
                gateway=getattr(loop, "gateway", None),
            )
    return _scoped_vaults


def _scoped_knowledge() -> tuple[Any, dict[str, Any] | None]:
    """The knowledge layer for THIS connection's scope, or an error receipt."""
    try:
        kb = _vaults().for_scope(_scope())
    except ValueError as e:
        return None, {"error": str(e), "httpStatus": 400}
    if kb is None or not hasattr(kb, "search"):
        return None, {"error": "knowledge layer unavailable"}
    return kb, None


def _identity() -> dict[str, str]:
    """Server-controlled tenant/workspace identity for this connection.

    HTTP principals are fixed by the bearer token. Stdio deployments may pin
    the richer identity with PREPENDE_MCP_TENANT / _WORKSPACE / _SCOPE; legacy
    ENGRAM aliases and scope-only setups remain compatible.
    """

    p = current_principal()
    if p and p.get("scope"):
        scope = validate_scope(p["scope"])
        return {
            "tenant": validate_scope(p.get("tenant") or scope),
            "workspace": validate_scope(p.get("workspace") or scope),
            "scope": scope,
        }
    tenant = _mcp_env("TENANT")
    workspace = _mcp_env("WORKSPACE")
    scope = _mcp_env("SCOPE")
    if tenant or workspace:
        if not tenant or not workspace:
            raise ValueError("MCP tenant and workspace must be configured together")
        scope = require_identity_namespace(tenant, workspace, scope)
    if not scope:
        runtime._brain()  # ensure config is loaded
        scope = getattr(runtime._cfg, "memory_scope", "default") or "default"
    scope = validate_scope(scope)
    return {
        "tenant": validate_scope(tenant or scope),
        "workspace": validate_scope(workspace or scope),
        "scope": scope,
    }


def _scope() -> str:
    return _identity()["scope"]


def _principal_receipt(identity: dict[str, str]) -> dict[str, Any]:
    """Return a stable, non-secret identity attestation for this connection."""

    principal = current_principal() or {}
    capabilities = sorted(name for name in ALL_TOOLS if is_allowed(name))
    fingerprint = str(principal.get("principalFingerprint") or "").strip()
    principal_id = str(principal.get("principalId") or "").strip()
    if not fingerprint or not principal_id:
        canonical = json.dumps(
            {
                "tenant": identity["tenant"],
                "workspace": identity["workspace"],
                "scope": identity["scope"],
                "capabilities": capabilities,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        fingerprint = "sha256:" + hashlib.sha256(canonical).hexdigest()
        principal_id = "mcp-stdio:" + fingerprint
    return {
        "principalId": principal_id,
        "principalFingerprint": fingerprint,
        "capabilities": capabilities,
    }


def _gate(tool: str) -> dict[str, Any] | None:
    """The ONE capability guard (threat T2). Returns a 403 receipt if this
    connection's PREPENDE_MCP_CAPABILITIES (or Engram alias) doesn't grant
    `tool`, else None. Default
    (unset) grants ALL tools; an untrusted connector is run with the SAFE subset,
    which excludes every write/action tool. List-returning tools check is_allowed
    directly and return []."""
    if is_allowed(tool):
        return None
    return {"error": "forbidden: capability '%s' not granted to this connection" % tool,
            "capability": tool, "httpStatus": 403}


def _capability_tool(capability: str | None = None):
    """Register an MCP tool with a mandatory dispatch-time capability gate."""

    def decorate(function):
        required = capability or function.__name__

        @functools.wraps(function)
        async def secured(*args, **kwargs):
            denied = _gate(required)
            if denied:
                return denied
            return await function(*args, **kwargs)

        registered = mcp.tool()(secured)
        # FastMCP/Pydantic otherwise ignores undeclared inputs.  That makes an
        # attempted caller-supplied ``scope`` look successful even though
        # identity is server-owned.  Fail closed on every unknown argument and
        # publish the same restriction in the MCP input schema.
        tool = mcp._tool_manager.get_tool(function.__name__)
        if tool is None:  # pragma: no cover - registration is synchronous
            raise RuntimeError(f"MCP tool registration failed: {function.__name__}")
        arg_model = tool.fn_metadata.arg_model
        arg_model.model_config["extra"] = "forbid"
        arg_model.model_rebuild(force=True)
        tool.parameters = arg_model.model_json_schema(by_alias=True)
        return registered
    return decorate


def _capability_resource(uri: str, capability: str):
    """Register an MCP resource with the same mandatory dispatch gate."""

    def decorate(function):
        @functools.wraps(function)
        async def secured(*args, **kwargs):
            denied = _gate(capability)
            if denied:
                return json.dumps(denied, sort_keys=True)
            return await function(*args, **kwargs)

        return mcp.resource(uri)(secured)
    return decorate


@_capability_tool()
async def chat(message: str) -> dict[str, Any]:
    """Talk to the Prepende brain. Routes to fast chat, the goal loop, or an
    approval-required refusal with a truthful receipt. Chat never writes
    durable memory. Explicit memory language can stage a review candidate only
    when this principal also has ``memory_propose``."""
    message = message.strip()
    if not message:
        return {"error": "empty message"}
    return await runtime.chat_async(
        _scope(),
        message,
        allow_memory_candidates=is_allowed("memory_propose"),
    )


@_capability_tool()
async def pursue_goal(goal: str) -> dict[str, Any]:
    """Run a goal through the full GoalLoop (strategist -> tactic -> resolver).
    Returns the answer plus the truthful run receipt; memory stays
    candidate-gated."""
    goal = goal.strip()
    if not goal:
        return {"error": "empty goal"}
    r = await runtime.run_goal_async(
        _scope(),
        goal,
        allow_memory_candidates=is_allowed("memory_propose"),
    )
    return {"answer": r["text"], "error": r["error"], "model": r["model"],
            "receipt": r["receipt"]}


@_capability_tool()
async def memory_search(query: str) -> dict[str, Any]:
    """Search this tenant's scoped memory. Returns hits (id, kind, content)
    and the count — read-only."""
    loop = runtime._brain()
    if loop.memory is None:
        return {"hits": [], "count": 0}
    hits = await loop.memory.search(query, scope=_scope(), k=10)
    out = [{"id": h.get("id"), "kind": h.get("kind", "episodic"), "content": h["content"]}
           for h in hits]
    return {"hits": out, "count": len(out)}


@_capability_tool()
async def remember(content: str, kind: str = "semantic") -> dict[str, Any]:
    """Durably write ONE memory the user explicitly asked to remember.
    Only call this to relay the user's own explicit statement ("remember X",
    "my Y is Z") — that statement IS the approval. For anything else use
    memory_propose."""
    denied = _gate("remember")
    if denied:
        return denied
    content = content.strip()
    if len(content) < 8:
        return {"written": None, "error": "content too short to be a real fact"}
    kind = kind if kind in _MEMORY_KINDS else "semantic"
    loop = runtime._brain()
    if loop.memory is None:
        return {"written": None, "error": "memory unavailable"}
    memory_id = await loop.memory.write(
        content, scope=_scope(),
        metadata={"source": "mcp_remember", "kind": kind,
                  "approval": "explicit_user_statement"},
    )
    return {"written": {"id": memory_id, "kind": kind}, "persisted": True}


@_capability_tool()
async def memory_propose(content: str, kind: str = "semantic") -> dict[str, Any]:
    """Stage a memory CANDIDATE without writing anything durable (the Assess
    gate). Use for inferred or derived facts; the candidate persists in the
    review queue, and promotion happens only through a separately authenticated
    owner approval surface — never through MCP and never here.

    Provenance is derived only from the authenticated, server-owned principal
    and transport. Callers cannot supply agent, connector, approval-path,
    tenant, scope, principal, or packet identity. A content hash is always
    stamped, so the owner approval path refuses a row that mutates before
    approval."""
    content = content.strip()
    kind = kind if kind in _MEMORY_KINDS else "semantic"
    if not content:
        return {"staged": False, "persisted": False, "durableWrite": False,
                "error": "content is empty"}
    # Intake gate: refuse deploy-blocklisted IP outright; FLAG injection markers so the
    # operator sees them in memory_candidates. Content is never executed — only staged.
    scan = scan_intake(content)
    if scan["blocked"]:
        return {"staged": False, "persisted": False, "durableWrite": False,
                "error": "refused: content contains blocked term(s): %s" % ", ".join(scan["blocked"])}
    identity = _identity()
    principal = _principal_receipt(identity)
    meta: dict[str, Any] = {
        "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "principal_id": principal["principalId"],
        "principal_fingerprint": principal["principalFingerprint"],
        "connector": "mcp_" + _transport(),
        "approval_path": "owner_approval_outside_mcp",
    }
    revision = deployment_revision()
    if revision:
        meta["deployment_revision"] = revision
    if scan["injection"]:
        meta["intake_flags"] = scan["injection"]
    candidate = await default_queue().propose(
        content, scope=_scope(), kind=kind, source="mcp_propose", metadata=meta)
    return {
        "staged": True,
        "persisted": False,
        "durableWrite": False,
        "status": "pending_assessment",
        "candidate": {"id": candidate["id"], "kind": candidate["kind"],
                      "content": candidate["content"][:1000]},
        "provenance": {
            "principalId": meta["principal_id"],
            "principalFingerprint": meta["principal_fingerprint"],
            "connector": meta["connector"],
            "approvalPath": meta["approval_path"],
            **({"deploymentRevision": revision} if revision else {}),
        },
        "flags": scan["injection"],
        "next": "owner approval outside MCP, or memory_reject",
    }


@_capability_tool()
async def memory_candidates() -> dict[str, Any]:
    """List this tenant's pending memory candidates awaiting approval.
    Read-only; surfaces the review queue so the user can decide."""
    pending = await default_queue().list_pending(scope=_scope())
    return {"pending": [{"id": c["id"], "kind": c["kind"], "content": c["content"],
                         "source": c["source"],
                         "flags": (c.get("metadata") or {}).get("intake_flags", [])}
                        for c in pending],
            "count": len(pending)}


@_capability_tool()
async def memory_reject(candidate_id: str, reason: str = "") -> dict[str, Any]:
    """Reject ONE pending candidate (it never becomes memory). Records the
    reason on the receipt."""
    denied = _gate("memory_reject")
    if denied:
        return denied
    rejected = await default_queue().reject(
        candidate_id.strip(), scope=_scope(), reason=reason)
    if rejected is None:
        return {"rejected": None, "error": "pending candidate not found for this tenant"}
    return {"rejected": {"candidateId": rejected["id"], "reason": rejected["reason"]},
            "persisted": False, "durableWrite": False}


@_capability_tool()
async def account() -> dict[str, Any]:
    """Redacted identity receipt: which tenant this MCP connection operates
    as, and under which gates. Never includes tokens or other tenants."""
    identity = _identity()
    return {
        "ok": True,
        **_principal_receipt(identity),
        "tenant": identity["tenant"],
        "tenantId": identity["tenant"],
        "workspace": identity["workspace"],
        "workspaceId": identity["workspace"],
        "scope": identity["scope"],
        "identity": "mcp",
        "model": getattr(runtime._brain().gateway, "name", "?"),
        "deploymentRevision": deployment_revision() or "unconfigured",
        "deploymentRevisionConfigured": deployment_revision() is not None,
        "memoryPolicy": "candidate",
        "externalActions": "approval_required",
    }


@_capability_tool()
async def list_workflows() -> list[dict[str, Any]]:
    """List registered workflows (names/descriptions only — URLs stay private)."""
    if not is_allowed("list_workflows"):
        return []
    wf = getattr(runtime._brain(), "workflows", None)
    return wf.list() if wf else []


@_capability_tool()
async def run_workflow(workflow: str = "", goal: str = "", params: dict | None = None) -> dict[str, Any]:
    """Stage a registered workflow run. Always dry_run + requiresApproval —
    nothing executes externally from this surface; the receipt says so."""
    denied = _gate("run_workflow")
    if denied:
        return denied
    status, obj = await runtime.run_workflow_async(
        _scope(), {"workflow": workflow, "goal": goal, "params": params or {}}
    )
    obj["httpStatus"] = status
    return obj


@_capability_tool()
async def knowledge_search(query: str) -> dict[str, Any]:
    """Hybrid search over THIS tenant's vault namespace (wiki + raw sources,
    per-scope RAG projection). Read-only; returns scored chunks with page and
    section provenance. A tenant token can never see another namespace."""
    query = query.strip()
    if not query:
        return {"hits": [], "count": 0}
    knowledge, err = _scoped_knowledge()
    if err:
        return {"hits": [], "count": 0, **err}
    hits = list(await knowledge.search(query, k=8))
    return {"hits": hits, "count": len(hits), "scope": _scope()}


@_capability_tool()
async def knowledge_related(page: str, depth: int = 1) -> dict[str, Any]:
    """Walk THIS tenant's wikilink graph around a page: inbound backlinks plus
    pages within `depth` hops — the vault's knowledge graph as a graph, not a
    text search. Read-only, namespace-bound."""
    page = page.strip()
    if not page:
        return {"page": "", "backlinks": [], "related": []}
    if not is_allowed("knowledge_related"):
        return {"error": "forbidden: capability 'knowledge_related' not granted to this connection",
                "capability": "knowledge_related", "httpStatus": 403}
    knowledge, err = _scoped_knowledge()
    if err:
        return err
    if not hasattr(knowledge, "related"):
        return {"error": "knowledge graph unavailable"}
    return {
        "page": page,
        "backlinks": list(await knowledge.backlinks(page)),
        "related": list(await knowledge.related(page, depth=max(1, min(3, depth)))),
        "scope": _scope(),
    }


@_capability_resource("memory://recent", "memory_search")
async def memory_recent() -> str:
    """The latest memories in this tenant's scope."""
    loop = runtime._brain()
    if loop.memory is None:
        return ""
    hits = await loop.memory.search("", scope=_scope(), k=10)
    return "\n\n".join(h["content"] for h in hits)


def _transport() -> str:
    return (_mcp_env("TRANSPORT") or "stdio").lower()


def main() -> None:
    # ``python -m interface.mcp_server`` bypasses the CLI wrapper, so establish
    # the same owner-only creation default before any runtime state is opened.
    enforce_private_umask()
    problem = startup_scope_guard()  # T15: refuse silent default-scope binding
    if problem:
        import sys
        print("Prepende MCP startup refused: " + problem, file=sys.stderr)
        sys.exit(2)
    if os.environ.get("PREPENDE_MCP_PREFLIGHT_ONLY", "").strip().lower() in {
        "1", "true", "yes",
    }:
        identity = _identity()
        print(json.dumps({
            "ok": True,
            "transport": _transport(),
            "tenant": identity["tenant"],
            "workspace": identity["workspace"],
            "scope": identity["scope"],
            "deploymentRevision": deployment_revision() or "unconfigured",
            "deploymentRevisionConfigured": deployment_revision() is not None,
            "capabilities": sorted(name for name in ALL_TOOLS if is_allowed(name)),
            "externalActions": "approval_required",
            "started": False,
            "preflightOnly": True,
        }, sort_keys=True))
        return
    if _transport() == "http":
        # Per-call bearer auth (T1) is mandatory over HTTP: anything reaching the port
        # must present a token that fixes its scope + capabilities. Wrap FastMCP's
        # Starlette app with the pure-ASGI auth middleware and serve it ourselves.
        import uvicorn
        from interface.mcp_http import auth_middleware
        app = auth_middleware(mcp.streamable_http_app())
        host = _mcp_env("HOST") or "127.0.0.1"  # containers set 0.0.0.0
        port = int(
            _mcp_env("PORT")
            or getattr(mcp.settings, "port", 8765)
        )
        uvicorn.run(app, host=host, port=port, log_level=str(mcp.settings.log_level).lower())
    else:
        mcp.run()  # stdio default — host pins the scope, no network auth (Rung-2A)


if __name__ == "__main__":
    main()
