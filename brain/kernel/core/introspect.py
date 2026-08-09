"""introspect — a snapshot of what the brain currently knows. See the brain.

Reads counts and recents across memory, the knowledge vault, and runs, for a
given tenant scope. Powers the TUI `brain` command and the /v1/state endpoint
(so a product can show "what Prepende knows about you"). Read-only; safe.
"""

from __future__ import annotations

from typing import Any


async def brain_state(loop: Any, scope: str = "default") -> dict:
    out: dict[str, Any] = {"scope": scope, "model": getattr(loop.gateway, "name", "?")}

    # Memory: count + a few recent items for this tenant.
    mem = getattr(loop, "memory", None)
    if mem is not None:
        try:
            recent = list(await mem.search("", scope=scope, k=5))  # empty query -> recency
            out["memory"] = {
                "recent_count": len(recent),
                "recent": [m["content"][:120] for m in recent],
                "backend": getattr(mem, "name", "?"),
            }
        except Exception as exc:
            out["memory"] = {"error": str(exc)}

    # Knowledge vault: page count + names.
    kb = getattr(loop, "knowledge", None)
    if kb is not None:
        try:
            pages = list(await kb.list_pages())
            out["knowledge"] = {"pages": len(pages), "titles": pages[:30]}
            # Tenant loops now have isolated vault recall, so readiness may be
            # shown but filesystem paths may not cross the HTTP/MCP boundary.
            rag = getattr(kb, "rag", None)
            if rag is not None and hasattr(rag, "status"):
                rag_status = rag.status()
                allowed = {
                    "source_files", "indexed_files", "chunks", "embedded_chunks",
                    "missing_embeddings", "configured_profile", "persisted_profile",
                    "actual_dimension", "lexical_ready", "semantic_ready", "stale",
                }
                out["knowledge"]["rag"] = {
                    key: value for key, value in rag_status.items() if key in allowed
                }
            graphify = getattr(loop, "graphify", None)
            if graphify is not None and hasattr(graphify, "status"):
                graph_status = graphify.status()
                out["knowledge"]["graphify"] = {
                    key: value for key, value in graph_status.items() if key != "path"
                }
            else:
                out["knowledge"]["graphify"] = {
                    "ready": False, "reason": "not_configured"
                }
        except Exception as exc:
            out["knowledge"] = {"error": str(exc)}

    # Runs: recent goal activity.
    runs = getattr(loop, "runs", None)
    if runs is not None:
        try:
            recent = runs.recent(5)
            out["runs"] = {
                "recent_count": len(recent),
                "recent": [{"goal": r["goal"][:80], "status": r["status"]} for r in recent],
            }
        except Exception as exc:
            out["runs"] = {"error": str(exc)}

    # Connectors: what the brain can reach.
    hub = getattr(loop, "connectors", None)
    if hub is not None:
        try:
            tools = list(await hub.list_tools(
                tenant_id=scope,
                workspace_id=getattr(loop, "workspace_id", scope),
            ))
            ready = sum(1 for t in tools if t.get("ready"))
            out["connectors"] = {"tools": len(tools), "ready": ready,
                                 "ids": [t["id"] for t in tools]}
        except Exception as exc:
            out["connectors"] = {"error": str(exc)}

    return out
