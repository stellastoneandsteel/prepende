"""ResearchAgent — finds high-quality info on a user-defined topic.

Gathers real candidate sources from a WIDE pool (tech + science feeds), ranks
them for relevance to THIS topic with embeddings — so each scout gets material on
its own question instead of the same generic headlines — then uses the model to
produce a structured, reviewable knowledge item: summary, key claims, confidence,
related entities, contradictions, and suggested follow-ups. Output ALWAYS lands as
`pending_review` — never auto-accepted.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
import urllib.parse
import urllib.request
from typing import Any, Sequence
from xml.etree import ElementTree as _ET

from connectors import news as _news

# Science / longform feeds the default tech-skewed news registry lacks. Kept HERE
# rather than in connectors/news.py so a product briefing (which reads
# the shared registry directly) is unaffected. The embedding ranker routes each
# scout to the right ones on its own — a biology question surfaces bioRxiv, a
# materials question surfaces cond-mat — so there is no hand-wired scout->feed map.
SCIENCE_FEEDS: list[dict[str, str]] = [
    {"url": "https://rss.arxiv.org/rss/cond-mat", "source": "arXiv cond-mat", "category": "Physics + Materials", "kind": "paper"},
    {"url": "https://rss.arxiv.org/rss/quant-ph", "source": "arXiv quant-ph", "category": "Quantum", "kind": "paper"},
    {"url": "https://rss.arxiv.org/rss/physics", "source": "arXiv physics", "category": "Physics", "kind": "paper"},
    {"url": "https://www.sciencedaily.com/rss/top/health.xml", "source": "ScienceDaily Health", "category": "Biology + Medicine", "kind": "article"},
    {"url": "https://www.sciencedaily.com/rss/plants_animals.xml", "source": "ScienceDaily Biology", "category": "Biology + Medicine", "kind": "article"},
    {"url": "https://www.quantamagazine.org/feed/", "source": "Quanta Magazine", "category": "Science Longform", "kind": "article"},
    {"url": "https://phys.org/rss-feed/", "source": "Phys.org", "category": "Physics + Materials", "kind": "article"},
]

_POOL_TTL = 300.0       # a round's scouts share ONE fetch (re-fetched at most every 5 min)
_POOL_MAX = 120         # cap on titles we embed per fetch
_POOL_PER_FEED = 6
_pool_cache: dict[str, Any] = {"ts": 0.0, "items": []}


def _fetch_feed(feed: dict, per_feed: int, timeout: int) -> list[dict]:
    try:
        req = urllib.request.Request(feed["url"], headers={"User-Agent": "Engram/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            xml = r.read().decode("utf-8", "replace")
        return _news.parse_rss(
            xml, source=feed.get("source", ""),
            category=feed.get("category", "News"), kind=feed.get("kind", "article"),
        )[:per_feed]
    except Exception:
        return []


def _pool(timeout: int = 8) -> list[dict]:
    """Wide candidate pool across the tech registry + science feeds, deduped and
    cached so every scout in a round reuses one fetch instead of hammering feeds.

    Assembled ROUND-ROBIN across feeds (one item from each, then the next from
    each) so the _POOL_MAX cap can't be monopolised by the many tech feeds and
    starve the science feeds — every feed is represented before the cap fills."""
    now = time.time()
    if _pool_cache["items"] and now - _pool_cache["ts"] < _POOL_TTL:
        return _pool_cache["items"]
    per_feed: list[list[dict]] = []
    for feed in _news.feed_registry() + SCIENCE_FEEDS:
        got = _fetch_feed(feed, _POOL_PER_FEED, timeout)
        if got:
            per_feed.append(got)
    seen: set[tuple[str, str]] = set()
    pool: list[dict] = []
    depth = 0
    while len(pool) < _POOL_MAX and any(depth < len(lst) for lst in per_feed):
        for lst in per_feed:
            if depth < len(lst):
                it = lst[depth]
                title = it.get("title", "")
                key = (title.lower(), it.get("url", ""))
                if title and key not in seen and not title.lower().startswith(_DIGEST_PREFIXES):
                    seen.add(key)
                    pool.append(it)
                    if len(pool) >= _POOL_MAX:
                        break
        depth += 1
    _pool_cache.update(ts=now, items=pool)
    return pool


_STOP = frozenset((
    "the a an of for and or to in on with from that what which recent results show real "
    "is are be by as at how do does this these those new their its into than rather not "
    "they them more most about over under can could would should may might use using "
    # research-meta words: they bloat the arXiv query and don't discriminate domain
    "seek specialized literature investigate peer reviewed papers paper explicitly "
    "search academic publication publications discussing find finding sources source "
    "candidate candidates study studies investigating explore exploring specific "
    "relevant context information data report reports article articles"
).split())


def _content_words(s: str) -> set[str]:
    """Topic-bearing tokens (>=3 chars, minus stopwords) for lexical overlap."""
    return {w for w in re.findall(r"[a-z0-9]+", (s or "").lower()) if len(w) >= 3 and w not in _STOP}


# Roundup/digest headlines (not a single story) that embed spuriously close to
# many queries. Dropped from the candidate pool so they can't crowd out real items.
_DIGEST_PREFIXES = ("the download", "daily briefing", "in brief", "week in review")


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    num = sum(x * y for x, y in zip(a, b))
    da = sum(x * x for x in a) ** 0.5
    db = sum(y * y for y in b) ** 0.5
    return num / (da * db) if da and db else 0.0


# ---- targeted arXiv search ----
# The daily RSS pool rarely carries the niche papers a fixed-domain literature watch
# (e.g. the prepende oscillator/Ising/reservoir study) actually asks for, so scouts
# loop on "I should find papers on X" and never do. This queries arXiv directly for
# the topic's key terms and returns real papers WITH abstracts, so a scout reasons
# over actual research instead of headlines. Best-effort; on any failure the scout
# just falls back to the RSS pool.
_ARXIV_API = "https://export.arxiv.org/api/query"
_ARXIV_NS = {"a": "http://www.w3.org/2005/Atom"}
# Optional hard domain lock. When set (e.g. by the autopilot for the prepende study),
# EVERY arXiv search is constrained to this fixed field — newest papers first — so a
# scout's results can never drift off-study even if its evolving question wanders.
# Example: 'all:"Ising machine" OR all:parametron OR all:"reservoir computing"'.
_ARXIV_ANCHOR = os.environ.get("AUTOPILOT_ARXIV_ANCHOR", "").strip()


def _arxiv_terms(topic: str, max_terms: int = 6) -> str:
    """The most informative content words (domain nouns over filler), longest first."""
    return " ".join(sorted(_content_words(topic), key=len, reverse=True)[:max_terms])


def _arxiv_search(topic: str, n: int = 8, timeout: int = 12) -> list[dict]:
    if _ARXIV_ANCHOR:
        search, sort, n = _ARXIV_ANCHOR, "submittedDate", max(n, 14)  # locked field, newest first
    else:
        terms = _arxiv_terms(topic)
        if not terms:
            return []
        search, sort = "all:" + terms, "relevance"
    q = urllib.parse.urlencode({
        "search_query": search, "start": 0, "max_results": n,
        "sortBy": sort, "sortOrder": "descending",
    })
    try:
        req = urllib.request.Request(f"{_ARXIV_API}?{q}", headers={"User-Agent": "Engram/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            root = _ET.fromstring(r.read().decode("utf-8", "replace"))
    except Exception:
        return []
    out: list[dict] = []
    for e in root.findall("a:entry", _ARXIV_NS):
        title = " ".join((e.findtext("a:title", "", _ARXIV_NS) or "").split())
        url = (e.findtext("a:id", "", _ARXIV_NS) or "").strip()
        aid = url.split("/abs/")[-1]
        abstract = " ".join((e.findtext("a:summary", "", _ARXIV_NS) or "").split())
        if title:
            out.append({"title": title, "source": f"arXiv:{aid}", "url": url,
                        "category": "Research Papers", "kind": "paper", "abstract": abstract})
    return out


class ResearchAgent:
    name = "research"

    def __init__(self, gateway: Any, items: Any) -> None:
        self.gateway = gateway
        self.items = items

    async def _rank(self, topic: str, pool: list[dict], limit: int) -> list[dict]:
        """Top-`limit` sources most relevant to `topic`, scored as embedding cosine
        PLUS a lexical term-overlap bonus — semantics catch paraphrase, exact shared
        terms ('superconductivity', 'genome', 'quantum') sharpen the domain match and
        demote vague headlines. Falls back to pure lexical if the provider has no
        embeddings (e.g. echo)."""
        titles = [p["title"] for p in pool]
        qtoks = _content_words(topic)
        lex = [len(qtoks & _content_words(t)) for t in titles]
        try:
            vecs = await self.gateway.embed([topic] + titles)
            if len(vecs) >= len(titles) + 1:
                q = vecs[0]
                scored = [
                    (p, _cosine(q, dv) + 0.04 * lx)
                    for p, dv, lx in zip(pool, vecs[1:1 + len(titles)], lex)
                ]
                scored.sort(key=lambda x: x[1], reverse=True)
                return [p for p, _ in scored[:limit]]
        except Exception:
            pass
        return [p for p, _ in sorted(zip(pool, lex), key=lambda x: x[1], reverse=True)[:limit]]

    async def _gather(self, topic: str, limit: int = 6) -> list[dict[str, Any]]:
        """Relevance-ranked candidate sources for THIS topic: the wide RSS pool PLUS a
        targeted arXiv search for the question's key terms (real papers + abstracts, so
        a literature-watch scout can actually advance instead of asking for papers it
        never gets). Up to half the slots are RESERVED for the queried arXiv papers so
        the 120-item RSS pool can't bury them in ranking; the rest fill from the pool."""
        rss = list(_pool())
        arxiv = await asyncio.to_thread(_arxiv_search, topic)  # off-loop: keeps scouts concurrent
        seen: set[str] = set()
        pool: list[dict] = []
        for s in rss + arxiv:
            k = s.get("title", "").lower()
            if k and k not in seen:
                seen.add(k)
                pool.append(s)
        if not pool:
            return []
        ranked = await self._rank(topic, pool, max(limit * 3, 18))
        # Reserve slots for the on-topic arXiv papers so the RSS pool can't bury them.
        # Domain-locked (anchor set): fill ALL slots from the field's papers, RSS only
        # as fallback. Otherwise reserve half and blend.
        arxiv_ranked = [s for s in ranked if s.get("source", "").startswith("arXiv")]
        reserve = limit if _ARXIV_ANCHOR else max(2, limit // 2)
        chosen: list[dict] = []
        picked: set[str] = set()
        for s in arxiv_ranked[:reserve] + ranked:
            k = s.get("title", "").lower()
            if k not in picked:
                picked.add(k)
                chosen.append(s)
            if len(chosen) >= limit:
                break
        return [{"title": s["title"], "source": s.get("source", ""), "url": s.get("url", ""),
                 "abstract": s.get("abstract", "")} for s in chosen]

    async def research(self, topic: str, *, scope: str = "default",
                       projects: list[str] | None = None) -> dict[str, Any]:
        sources = await self._gather(topic)

        def _fmt(s: dict) -> str:
            line = f"- {s['title']} ({s['source']}) {s.get('url', '')}"
            if s.get("abstract"):  # arXiv papers carry the abstract — reason over real content
                line += f"\n    abstract: {s['abstract'][:500]}"
            return line

        src_block = "\n".join(_fmt(s) for s in sources) or "(no live sources fetched)"
        prompt = (
            f"You are a research scout. Topic: {topic}\n\n"
            f"Candidate sources (papers include their abstract):\n{src_block}\n\n"
            "Return STRICT JSON only (no prose, no fences):\n"
            '{"summary": str, "claims": [str], "confidence": 0.0-1.0, '
            '"related_entities": [str], "contradictions": str, "follow_ups": [str]}\n'
            "Base claims ONLY on the sources/abstracts above. Prefer the papers. If they "
            "don't address the topic, say so and lower confidence — do not invent."
        )
        raw = await self.gateway.complete([{"role": "user", "content": prompt}], max_tokens=900)
        data = _parse_json(raw)

        best = sources[0] if sources else {}
        item_id = self.items.add(
            scope=scope, topic=topic, agent=self.name, state="pending_review",
            title=data.get("summary", topic)[:120] or topic,
            source_url=best.get("url", ""), author=best.get("source", ""),
            summary=data.get("summary", raw[:500]),
            claims=data.get("claims", []),
            confidence=float(data.get("confidence", 0.3) or 0.3),
            related_entities=data.get("related_entities", []),
            related_projects=projects or [],
            contradiction=data.get("contradictions", "none") or "none",
            relevance=0.5,
        )
        return {"item_id": item_id, "sources": len(sources),
                "follow_ups": data.get("follow_ups", []), "summary": data.get("summary", raw[:300])}


def _parse_json(text: str) -> dict[str, Any]:
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        t = t[t.find("{"):]
    try:
        return json.loads(t[t.find("{"): t.rfind("}") + 1])
    except Exception:
        return {}
