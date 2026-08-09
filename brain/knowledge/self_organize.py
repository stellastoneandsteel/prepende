"""Self-organization SCAN — the detection half of a self-healing brain.

Deterministic, READ-ONLY, no model: surfaces what the brain should tidy (orphans,
dangling links, stale pages, the hottest dangling targets) so a scheduled pass can
PROPOSE fixes for human review. It never writes vault structure — proposals only;
acting on them stays a human/gated decision (the brain proposes, it does not decay
silently and it does not auto-edit itself).

Pairs with knowledge/vault.py lint() (orphan/dangling detection) and the existing
brain_heal.py (RAG refresh + opt-in consolidate + backup). Stdlib only.
"""

from __future__ import annotations

import os
import sqlite3
import time
from collections import Counter
from typing import Any


async def scan(vk: Any, *, stale_days: int = 30, memory_db: str | None = None) -> dict[str, Any]:
    """Read-only health scan of the vault (+ an optional memory summary). Returns a
    structured findings dict — no writes, no model calls."""
    issues = list(await vk.lint())
    orphans = sorted(i["page"] for i in issues if i.get("type") == "orphan")
    dangling = [(i["from"], i["to"]) for i in issues if i.get("type") == "dangling_link"]
    dangling_freq = Counter(t for _, t in dangling)

    now = time.time()
    stale = []
    pages = list(vk.wiki.glob("*.md"))
    for p in pages:
        age = (now - p.stat().st_mtime) / 86400.0
        if age > stale_days:
            stale.append((p.stem, int(age)))
    stale.sort(key=lambda x: -x[1])

    return {
        "page_count": len(pages),
        "orphans": orphans,
        "dangling_count": len(dangling),
        "dangling_top": dangling_freq.most_common(15),
        "stale_days": stale_days,
        "stale": stale,
        "memory": _memory_summary(memory_db) if memory_db else {},
    }


def _memory_summary(memory_db: str) -> dict[str, Any]:
    """Best-effort read-only counts of the memory store (for the dedup pointer)."""
    if not os.path.exists(memory_db):
        return {}
    try:
        con = sqlite3.connect(f"file:{memory_db}?mode=ro", uri=True)
        try:
            rows = con.execute(
                "SELECT scope, COUNT(*) FROM memories WHERE status != 'deleted' GROUP BY scope"
            ).fetchall()
        except Exception:
            rows = con.execute("SELECT scope, COUNT(*) FROM memories GROUP BY scope").fetchall()
        con.close()
        return {"by_scope": {r[0]: r[1] for r in rows}, "total": sum(r[1] for r in rows)}
    except Exception:
        return {}


def proposals(findings: dict[str, Any]) -> list[str]:
    """Turn raw findings into human-actionable PROPOSALS (strings). No writes."""
    out: list[str] = []
    for o in findings.get("orphans", []):
        out.append(f"ORPHAN: [[{o}]] has no inbound link — link it from a topic hub.")
    for target, n in findings.get("dangling_top", []):
        out.append(f"DANGLING ({n}x): [[{target}]] is linked but doesn't exist — create a stub or de-link.")
    for stem, age in findings.get("stale", [])[:15]:
        out.append(f"STALE ({age}d): [[{stem}]] hasn't changed in a while — review for freshness.")
    return out
