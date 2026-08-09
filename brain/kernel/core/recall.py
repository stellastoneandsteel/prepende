"""Unified recall — one associative read over everything the brain knows.

Memory (the fast operational store) and the vault (the curated wiki) used to
be separate recall paths: a goal searched memory only, and the wiki's link
graph was a browsing aid. This module merges them: hybrid memory search, plus
the vault's RAG hits, plus a budgeted one-hop walk of the wikilink graph out
from the matched pages — so a goal can surface a curated neighbor fact that
neither keyword nor vector matching alone would have returned. Independent
memory and vault reads run concurrently, then a deterministic edge fuses,
deduplicates, and globally budgets their outputs before any model sees them.
Every recalled item is a dict with 'content' (tactics fold them through the
MEMORY_GUARD preamble: data, never instructions) and every stage fails safe —
a vault error degrades the read to memory-only, never crashes the run.

Vault recall is opt-in: GoalLoop(vault_recall=True). build_brain enables it for
the owner brain, and hosted surfaces enable it only with a namespace returned by
ScopedVaults. Graphify remains owner-only, so a repository/corpus projection can
never enter tenant chat.
"""

from __future__ import annotations

import asyncio
import re
from collections import deque
from typing import Any

_VAULT_K = 3       # top wiki chunks folded into recall
_NEIGHBOR_K = 2    # wikilink-neighbor pages beyond the direct hits
_GRAPHIFY_K = 2    # audited Graphify nodes/edges per tier (direct + one-hop)
_EXCERPT = 480     # chars per excerpt — recall is a hint, not a page dump
_MAX_RECALL_ITEMS = 32

# Direct operational memory and reviewed wiki hits get more turns than
# associative neighbors and the optional Graphify projection. This preserves
# source diversity without letting an advisory graph crowd out source facts.
_FUSION_SCHEDULE = (
    "memory", "vault", "memory", "vault", "graphNeighbors", "graphify",
)
_SOURCE_ORDER = ("memory", "vault", "graphNeighbors", "graphify")

_FRONTMATTER = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)


def _excerpt(text: str) -> str:
    body = " ".join(_FRONTMATTER.sub("", text or "").split())
    if len(body) <= _EXCERPT:
        return body
    cut = body.rfind(" ", 0, _EXCERPT)
    return body[: cut if cut > 200 else _EXCERPT].rstrip() + " …"


def _content_key(item: Any) -> str:
    """Exact, formatting-insensitive identity for deterministic deduplication.

    This deliberately avoids model-powered or fuzzy dedupe: two related facts
    are allowed through, while byte-equivalent repeats cannot waste context.
    """

    if not isinstance(item, dict):
        return ""
    content = item.get("content")
    if not isinstance(content, str) or not content.strip():
        return ""
    return " ".join(content.casefold().split())


def _fuse_recall(
    buckets: dict[str, list[dict[str, Any]]], *, max_items: int,
) -> tuple[list[dict[str, Any]], dict[str, int], dict[str, Any]]:
    """Validate, dedupe, and budget recall with deterministic source fairness."""

    retrieved_sources = {
        source: len(buckets.get(source, []))
        for source in _SOURCE_ORDER
        if source in buckets
    }
    queues: dict[str, deque[dict[str, Any]]] = {}
    seen: set[str] = set()
    duplicates_dropped = 0
    invalid_dropped = 0

    # Prefer the canonical operational/reviewed layers when the same exact
    # content appears in an advisory layer later in the source order.
    for source in _SOURCE_ORDER:
        if source not in buckets:
            continue
        unique: deque[dict[str, Any]] = deque()
        for item in buckets[source]:
            key = _content_key(item)
            if not key:
                invalid_dropped += 1
                continue
            if key in seen:
                duplicates_dropped += 1
                continue
            seen.add(key)
            unique.append(item)
        queues[source] = unique

    limit = max(0, min(int(max_items), _MAX_RECALL_ITEMS))
    selected: list[dict[str, Any]] = []
    selected_sources = {source: 0 for source in retrieved_sources}
    while len(selected) < limit and any(queues.values()):
        progressed = False
        for source in _FUSION_SCHEDULE:
            queue = queues.get(source)
            if not queue:
                continue
            selected.append(queue.popleft())
            selected_sources[source] += 1
            progressed = True
            if len(selected) >= limit:
                break
        if not progressed:  # defensive: every known bucket is in the schedule
            break

    budget_dropped = sum(len(queue) for queue in queues.values())
    selection = {
        "maxItems": limit,
        "retrieved": sum(retrieved_sources.values()),
        "returned": len(selected),
        "duplicatesDropped": duplicates_dropped,
        "invalidDropped": invalid_dropped,
        "budgetDropped": budget_dropped,
        "retrievedSources": retrieved_sources,
    }
    return selected, selected_sources, selection


async def unified_recall(goal_text: str, *, memory: Any = None, knowledge: Any = None,
                         graphify: Any = None, scope: str = "default", k: int = 5,
                         vault: bool = False, max_items: int | None = None) -> dict[str, Any]:
    """Return memory-shaped items plus truthful per-source counts.

    The optional Graphify tier is owner-only and read-only; stale or malformed
    artifacts contribute zero items rather than weakening ordinary recall.
    """
    async def memory_recall() -> list[dict[str, Any]]:
        if memory is None:
            return []
        try:
            return list(await memory.search(goal_text, scope=scope, k=k))
        except Exception:
            return []

    async def vault_recall() -> dict[str, list[dict[str, Any]]]:
        if not vault or knowledge is None:
            return {"vault": [], "graphNeighbors": []}
        return await _vault_recall(goal_text, knowledge)

    # These reads share no data edge, so neither waits for the other. Graphify
    # remains downstream because its ranking consumes the direct vault paths.
    memory_items, vault_buckets = await asyncio.gather(memory_recall(), vault_recall())
    buckets: dict[str, list[dict[str, Any]]] = {
        "memory": memory_items,
        "vault": vault_buckets["vault"],
        "graphNeighbors": vault_buckets["graphNeighbors"],
    }
    if vault and graphify is not None:
        try:
            graph_items = list(await graphify.recall(
                goal_text,
                source_hints=[item.get("path", "") for item in vault_buckets["vault"]],
                k=_GRAPHIFY_K,
                neighbor_k=_GRAPHIFY_K,
            ))
        except Exception:
            graph_items = []
        buckets["graphify"] = graph_items

    budget = max_items if max_items is not None else max(0, k) + _VAULT_K + _NEIGHBOR_K
    items, sources, selection = _fuse_recall(buckets, max_items=budget)
    return {"items": items, "sources": sources, "selection": selection}


async def _vault_recall(
    goal_text: str, knowledge: Any,
) -> dict[str, list[dict[str, Any]]]:
    direct: list[dict[str, Any]] = []
    neighbors: list[dict[str, Any]] = []
    try:
        chunks = list(await knowledge.search(goal_text, k=_VAULT_K))
    except Exception:
        return {"vault": direct, "graphNeighbors": neighbors}
    pages_hit: list[str] = []
    for ch in chunks[:_VAULT_K]:
        page = str(ch.get("page") or "")
        body = _excerpt(str(ch.get("content") or ""))
        if not page or not body:
            continue
        if page not in pages_hit:
            pages_hit.append(page)
        section = str(ch.get("section") or "").strip()
        label = f"wiki [[{page}]]" + (f" § {section}" if section else "")
        direct.append({"content": f"({label}) {body}", "source": "vault", "page": page,
                       "path": str(ch.get("path") or ""), "score": ch.get("score")})
    if not pages_hit:
        return {"vault": direct, "graphNeighbors": neighbors}
    # The associative read: one hop out along the wikilink graph from the pages
    # the search matched, nearest first. Bounded to _NEIGHBOR_K pages so a hub
    # with forty links can never flood the prompt.
    try:
        for page in pages_hit:
            if len(neighbors) >= _NEIGHBOR_K:
                break
            for nbr in await knowledge.related(page, depth=1):
                if len(neighbors) >= _NEIGHBOR_K:
                    break
                if nbr in pages_hit or any(o.get("page") == nbr for o in direct + neighbors):
                    continue
                body = _excerpt(await knowledge.read_page(nbr))
                if not body:
                    continue
                neighbors.append({
                    "content": f"(wiki neighbor [[{nbr}]], linked from [[{page}]]) {body}",
                    "source": "vault_graph", "page": nbr,
                })
    except Exception:
        pass  # the direct hits still stand
    return {"vault": direct, "graphNeighbors": neighbors}
