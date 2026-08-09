"""Heartbeat — the one-pass maintenance cycle that makes "self-organizing" true.

The vault reindex, embedding backfill, memory consolidation, and the site
scraper all existed as on-demand calls; nothing ran them on a schedule, so
"self-organizing" stayed aspirational. This module is the missing pulse: a
scheduler (cron on Railway, launchd locally) invokes one pass and the process exits.

Tasks per pass (PREPENDE_HEARTBEAT_TASKS, default "vault,embed,consolidate"):
  vault        refresh the vault RAG projection until embedding backfill
               converges (refresh() caps at 64/pass by design; we loop it)
  embed        memory embed_backfill per configured scope, looped to zero
  consolidate  memory consolidation per scope (near-duplicate clusters
               distilled; the store's own gates apply)
  scrape       scrape-watch pass over PREPENDE_WATCHED_SITES — opt-in (network
               fetches); stages Assess CANDIDATES only, never writes memory

Scopes come from config, never SQL discovery (the RLS design rule):
PREPENDE_HEARTBEAT_SCOPES comma list, default the brain's own MEMORY_SCOPE.

Every pass appends a truthful JSON receipt to .engram/heartbeat.jsonl and
prints it. A task failure is recorded in the receipt and the pass continues —
one broken subsystem must not silence the rest of the heartbeat. Nothing here
calls the generation model or executes external actions.

Run:    python3 -m services.heartbeat
Deploy: second Railway service on this repo, start command
        `python3 -m services.heartbeat`, cron schedule (e.g. hourly).
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

from prepende_brain.env import brand_env
from prepende_brain.private_fs import append_private_text

_DEFAULT_TASKS = ("vault", "embed", "consolidate")
_MAX_ROUNDS = 50  # bounds the convergence loops; a pass must always terminate
_JOURNAL = "./.engram/heartbeat.jsonl"


def _tasks() -> list[str]:
    raw = brand_env("HEARTBEAT_TASKS", ",".join(_DEFAULT_TASKS))
    return [t.strip() for t in raw.split(",") if t.strip()]


def _scopes(default_scope: str) -> list[str]:
    raw = brand_env("HEARTBEAT_SCOPES", default_scope)
    return [s.strip() for s in raw.split(",") if s.strip()]


async def _vault_task(knowledge: Any) -> dict[str, Any]:
    """Refresh until the chunk-embedding backfill converges (or the embedder
    is down — backfilled stays 0 and we stop instead of spinning)."""
    total = {"rounds": 0, "reindexed": 0, "backfilled": 0}
    for _ in range(_MAX_ROUNDS):
        r = await knowledge.rag.refresh()
        total["rounds"] += 1
        total["reindexed"] += r.get("reindexed", 0)
        total["backfilled"] += r.get("backfilled", 0)
        if not r.get("backfilled"):
            break
    return total


async def _embed_task(memory: Any, scopes: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if not hasattr(memory, "embed_backfill"):
        return {"skipped": "store has no embed_backfill"}
    for scope in scopes:
        total = {"embedded": 0, "remaining": 0}
        for _ in range(_MAX_ROUNDS):
            r = await memory.embed_backfill(scope=scope)
            total["embedded"] += r["embedded"]
            total["remaining"] = r["remaining"]
            if r["remaining"] == 0 or r["embedded"] == 0:
                break  # converged, or the embedder is failing right now
        out[scope] = total
    return out


async def _consolidate_task(memory: Any, scopes: list[str]) -> dict[str, Any]:
    if not hasattr(memory, "consolidate"):
        return {"skipped": "store has no consolidate"}
    return {scope: await memory.consolidate(scope=scope) for scope in scopes}


async def _scrape_task(loop: Any) -> dict[str, Any]:
    from knowledge.scrape_watch import scrape_all, watched_sites
    if not watched_sites():
        return {"skipped": "PREPENDE_WATCHED_SITES empty"}
    results = await scrape_all(loop)
    return {"pages": len(results),
            "changed": sum(1 for r in results if r.get("changed")),
            "staged": sum(r.get("staged", 0) for r in results),
            "errors": [r["url"] for r in results if not r.get("ok")]}


async def run_pass(loop: Any = None, *, tasks: list[str] | None = None,
                   scopes: list[str] | None = None,
                   journal: str | None = None) -> dict[str, Any]:
    """One heartbeat. `loop` is the brain (injected in tests); built from the
    composition root when absent. Returns the receipt it journaled."""
    if loop is None:
        from kernel.core.brain import build_brain
        loop, cfg, _gw = build_brain()
        scopes = scopes or _scopes(cfg.memory_scope)
    scopes = scopes or _scopes(getattr(loop, "scope", "default"))
    tasks = tasks if tasks is not None else _tasks()

    started = time.time()
    receipt: dict[str, Any] = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "tasks": {}, "scopes": scopes,
    }
    runners = {
        "vault": lambda: _vault_task(loop.knowledge),
        "embed": lambda: _embed_task(loop.memory, scopes),
        "consolidate": lambda: _consolidate_task(loop.memory, scopes),
        "scrape": lambda: _scrape_task(loop),
    }
    for name in tasks:
        runner = runners.get(name)
        if runner is None:
            receipt["tasks"][name] = {"error": "unknown task"}
            continue
        try:
            receipt["tasks"][name] = await runner()
        except Exception as exc:  # one broken subsystem must not stop the pulse
            receipt["tasks"][name] = {"error": f"{type(exc).__name__}: {exc}"}
    receipt["durationMs"] = int((time.time() - started) * 1000)

    path = Path(journal or brand_env("HEARTBEAT_JOURNAL") or _JOURNAL)
    append_private_text(
        path,
        json.dumps(receipt) + "\n",
        repair_parent=path.parent.name == ".engram",
    )
    return receipt


def main() -> None:
    receipt = asyncio.run(run_pass())
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
