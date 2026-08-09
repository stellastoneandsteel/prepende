"""Compounding brain — gated mechanisms for the brain to grow DENSER, not just bigger.

Two propose-only capabilities (NO autonomous agent loop — that wiring is a larger effort;
these are inert until invoked and never auto-write the brain):

  - detect_gaps: queries whose recall is thin -> a knowledge-gap proposal (a research goal
    a human/scout can pursue). Read-only.
  - propose_synthesis: a topic page + its graph-neighbours -> a DRAFT canonical note WITH
    provenance ([[source]] links), returned for human/gated review — NOT written to the vault.

Builds on VaultKnowledge.search()/related()/read_page(). Stdlib + the model gateway.
"""

from __future__ import annotations

from typing import Any


async def detect_gaps(knowledge: Any, queries: list[str], *, min_hits: int = 2) -> list[dict[str, Any]]:
    """Queries that recall fewer than `min_hits` pages are gaps the brain can't answer well.
    Returns proposals only — does not trigger any research."""
    gaps: list[dict[str, Any]] = []
    for q in queries:
        try:
            hits = list(await knowledge.search(q, k=5))
        except Exception:
            hits = []
        if len(hits) < min_hits:
            gaps.append({"query": q, "hits": len(hits),
                         "proposed_goal": "Research and add a note answering: %s" % q})
    return gaps


async def propose_synthesis(knowledge: Any, topic_page: str, *, gateway: Any,
                            depth: int = 1, max_sources: int = 6) -> dict[str, Any] | None:
    """Gather a topic page + its graph-neighbours and DRAFT one canonical synthesis note
    with provenance. Returns {topic, sources, draft} for review — never writes the vault.
    None if there's nothing to synthesize or no gateway."""
    if gateway is None:
        return None
    related = list(await knowledge.related(topic_page, depth=depth))[:max_sources]
    sources = [topic_page] + related
    excerpts = []
    for s in sources:
        try:
            txt = await knowledge.read_page(s)
        except Exception:
            txt = ""
        if txt.strip():
            excerpts.append("## [[%s]]\n%s" % (s, txt.strip()[:1200]))
    if not excerpts:
        return None
    prompt = ("Synthesize these related notes into ONE concise canonical wiki note (150-250 words). "
              "Be honest, no overclaiming, label estimates as estimates. Start with a single "
              "'# Title' line, and END with a '## Sources' list of the [[page]] links you drew on. "
              "Notes:\n\n" + "\n\n".join(excerpts))
    draft = await gateway.complete([{"role": "user", "content": prompt}], max_tokens=700)
    return {"topic": topic_page, "sources": sources, "draft": (draft or "").strip()}
