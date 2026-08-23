"""Brain self-service — paste/upload -> structured draft -> diff -> approval (W6).

The client (or the self-scraper) hands over raw text — a price sheet, a
services page, intake notes. This module turns it into a REVIEWABLE DRAFT:

  1. extract candidate facts (deterministic line/sentence splitting — works
     with zero model; a model pass can refine later, never replace the gate);
  2. diff each fact against the tenant's current memory:
       unchanged — the brain already knows this (not staged);
       update    — overlaps an existing memory enough to be its successor
                   (staged with metadata.supersedes = that memory's id);
       new       — no meaningful overlap (staged fresh);
  3. stage new/update facts as Assess CANDIDATES (source carries provenance,
     e.g. "brain_update.paste" or "site_scrape:<url>").

NOTHING becomes durable memory here. Approval rides the W3 lane
(/v1/memory/candidates/{id}); when an approved candidate carries
metadata.supersedes, promotion SUPERSEDES the old fact instead of writing a
duplicate — temporal validity, never overwrite (memory/candidates.py).
"""

from __future__ import annotations

import re
from typing import Any

from memory.candidates import default_queue

_WORD_RE = re.compile(r"[^a-z0-9]+")
_MAX_FACTS = 100

# Diff thresholds: ratio = |shared significant words| / |fact's words|.
UNCHANGED_RATIO = 0.9
UPDATE_RATIO = 0.45
UPDATE_MIN_SHARED = 4


def _words(text: str) -> set[str]:
    return {w for w in _WORD_RE.split(str(text).lower()) if len(w) > 2}


def extract_facts(text: str) -> list[str]:
    """Deterministic fact extraction: bullet/line items first, then sentences
    for prose paragraphs. No model required — the draft must be inspectable
    even when no provider is configured."""
    facts: list[str] = []
    for raw_line in (text or "").splitlines():
        line = re.sub(r"^[\s\-\*•\d\.\)]+", "", raw_line).strip()
        if not line:
            continue
        if len(line) > 240 and line.count(". ") >= 1:
            for sent in re.split(r"(?<=[.!?])\s+", line):
                sent = sent.strip()
                if len(sent) >= 8:
                    facts.append(sent[:500])
        elif len(line) >= 8:
            facts.append(line[:500])
        if len(facts) >= _MAX_FACTS:
            break
    return facts[:_MAX_FACTS]


async def classify_fact(memory: Any, scope: str, fact: str) -> dict[str, Any]:
    """Diff one fact against current memory: unchanged | update | new."""
    fact_words = _words(fact)
    if not fact_words:
        return {"action": "unchanged", "fact": fact, "reason": "no significant words"}
    hits: list[dict] = []
    if memory is not None:
        try:
            hits = [h for h in await memory.search(fact, scope=scope, k=3) if isinstance(h, dict)]
        except Exception:
            hits = []
    best, best_ratio, best_shared = None, 0.0, 0
    for h in hits:
        shared = fact_words & _words(h.get("content", ""))
        ratio = len(shared) / len(fact_words)
        if ratio > best_ratio:
            best, best_ratio, best_shared = h, ratio, len(shared)
    if best is not None and best_ratio >= UNCHANGED_RATIO:
        return {"action": "unchanged", "fact": fact, "matches": best["id"],
                "overlap": round(best_ratio, 2)}
    if best is not None and (best_ratio >= UPDATE_RATIO or best_shared >= UPDATE_MIN_SHARED):
        return {"action": "update", "fact": fact, "supersedes": best["id"],
                "overlap": round(best_ratio, 2), "current": str(best.get("content", ""))[:300]}
    return {"action": "new", "fact": fact}


async def draft_update(brain: Any, scope: str, text: str, *, source: str = "brain_update.paste") -> dict[str, Any]:
    """The full draft: extract, diff, stage. Returns the diff receipt the
    client reviews; approval happens per-candidate through the W3 lane."""
    facts = extract_facts(text)
    items: list[dict] = []
    counts = {"new": 0, "update": 0, "unchanged": 0}
    queue = default_queue()
    for fact in facts:
        item = await classify_fact(brain.memory, scope, fact)
        counts[item["action"]] += 1
        if item["action"] in ("new", "update"):
            meta: dict[str, Any] = {"lane": "brain_update"}
            if item["action"] == "update":
                meta["supersedes"] = item["supersedes"]
            staged = await queue.propose(
                fact, scope=scope, kind="semantic", source=source, metadata=meta)
            item["candidateId"] = staged["id"]
        items.append(item)
    return {
        "ok": True,
        "tenantId": scope,
        "source": source,
        "factsExtracted": len(facts),
        "counts": counts,
        "items": items,
        "durableWrite": False,
        "externalActions": "none",
        "honesty": "This is a staged draft. Nothing becomes memory until each "
                   "item is approved in the review queue.",
        "next": "Review and decide each candidateId via POST /v1/memory/candidates/{id}.",
    }
