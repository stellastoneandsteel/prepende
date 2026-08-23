"""VaultKnowledge — the self-organizing wiki over the Obsidian vault. Stdlib + the model.

Implements kernel.contracts.Knowledge over a plain markdown vault (the Karpathy
"LLM wiki" pattern): the model compiles a source into a wiki page with YAML
frontmatter and [[wikilinks]], and the kernel keeps index.md + log.md current.
The vault is the source of truth; the kernel reads/writes the files directly (no
Obsidian app needed). Curated, human-readable, git-diffable — distinct from the
fast operational MemoryStore.
"""

from __future__ import annotations

import datetime
import hashlib
import re
from pathlib import Path
from typing import Any, Sequence

from prepende_brain.private_fs import secure_directory, secure_file
from prepende_brain.env import brand_env

from kernel.contracts import Knowledge

_LINK = re.compile(r"\[\[([^\]]+)\]\]")


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (s or "note")[:60]


# Tiered Map-of-Content sectioning for index.md — hubs first, then by role, so the
# index reads as an organized map instead of a flat alphabetical dump. Driven by the
# generic `type` frontmatter + GENERIC role keywords — no specific tenant/customer names
# in the substrate (those would be a SEPARATION smell). A deployment can extend the
# keyword sets via PREPENDE_VAULT_OPS_HINTS / PREPENDE_VAULT_RESEARCH_HINTS
# (legacy Engram aliases remain accepted).
_OPS_HINTS = ("tenant", "business", "launch", "publish", "ingestion", "auto-pilot",
              "operations", "harness", "client", "onboarding") + tuple(
    h.strip() for h in brand_env("VAULT_OPS_HINTS").split(",") if h.strip())
_RESEARCH_HINTS = ("oim", "ising", "reservoir", "narma", "max-cut", "maximum-cut",
                   "simulated-annealing", "thesis", "perpendicular", "coupled-oscillator",
                   "big-n", "oscillator", "benchmark", "experiment", "prepende") + tuple(
    h.strip() for h in brand_env("VAULT_RESEARCH_HINTS").split(",") if h.strip())
_SECTION_ORDER = ("Maps of Content", "Research", "Operations & Tenants", "System & Architecture")


def _section(stem: str, page_type: str) -> str:
    if page_type == "hub":
        return "Maps of Content"
    if page_type == "tenant":
        return "Operations & Tenants"
    s = stem.lower()
    if any(h in s for h in _OPS_HINTS):
        return "Operations & Tenants"
    if any(h in s for h in _RESEARCH_HINTS):
        return "Research"
    return "System & Architecture"


class VaultKnowledge(Knowledge):
    def __init__(self, vault_path: str = "./vault", gateway: Any = None) -> None:
        self.root = Path(vault_path)
        self.wiki = self.root / "wiki"
        secure_directory(self.root)
        secure_directory(self.wiki)
        secure_directory(self.root / "raw")
        self.gateway = gateway
        from knowledge.rag import VaultRagIndex
        self.rag = VaultRagIndex(vault_path)

    def set_embedder(
        self,
        embedder: Any,
        *,
        profile: str = "",
        expected_dimension: int | None = None,
    ) -> dict[str, Any]:
        return self.rag.set_embedder(
            embedder,
            profile=profile,
            expected_dimension=expected_dimension,
        )

    async def search(self, query: str, k: int = 8) -> Sequence[Any]:
        """Hybrid search over the vault's RAG projection. Refreshes changed
        files first so results track the markdown, never a stale index."""
        await self.prepare_search()
        return await self.search_prepared(query, k=k)

    async def prepare_search(self) -> None:
        """Refresh once before a bounded parallel retrieval batch.

        Query-evidence graphs call this before fanning out so concurrent nodes
        share one certified index snapshot instead of contending on one refresh
        lock per query.
        """
        await self.rag.refresh()

    async def search_prepared(self, query: str, k: int = 8) -> Sequence[Any]:
        """Search an index already refreshed by the current retrieval run."""
        return await self.rag.search(query, k=k)

    def _page_path(self, page_id: str) -> Path:
        return self.wiki / f"{page_id}.md"

    async def read_page(self, page_id: str) -> str:
        p = self._page_path(page_id)
        return p.read_text() if p.exists() else ""

    async def write_page(self, page_id: str, content: str) -> None:
        page = self._page_path(page_id)
        page.write_text(content)
        secure_file(page, required=True)
        await self._reindex()

    async def list_pages(self) -> Sequence[str]:
        return sorted(p.stem for p in self.wiki.glob("*.md"))

    async def parse_links(self, page_id: str) -> Sequence[str]:
        return [m.strip() for m in _LINK.findall(await self.read_page(page_id))]

    async def link_graph(self) -> dict[str, set[str]]:
        """The forward wikilink graph over EXISTING pages: {page_id: {pages it links to}}.
        Dangling links (to non-existent pages) are dropped — this is the navigable graph."""
        pages = {p.stem for p in self.wiki.glob("*.md")}
        graph: dict[str, set[str]] = {p: set() for p in pages}
        for p in pages:
            text = (self.wiki / f"{p}.md").read_text(encoding="utf-8", errors="replace")
            for raw in _LINK.findall(text):
                target = _slug(raw.split("|")[0])
                if target in pages and target != p:
                    graph[p].add(target)
        return graph

    async def backlinks(self, page_id: str) -> list[str]:
        """Pages that link TO page_id (inbound) — what references this page."""
        graph = await self.link_graph()
        return sorted(src for src, dsts in graph.items() if page_id in dsts)

    async def related(self, page_id: str, depth: int = 1) -> list[str]:
        """Pages within `depth` hops of page_id over the undirected link graph, nearest
        first — 'show me everything related to X' as a real graph walk, not a text guess."""
        graph = await self.link_graph()
        undirected: dict[str, set[str]] = {p: set(d) for p, d in graph.items()}
        for src, dsts in graph.items():
            for d in dsts:
                undirected.setdefault(d, set()).add(src)
        seen, frontier, out = {page_id}, {page_id}, []
        for _ in range(max(1, depth)):
            nxt = set()
            for node in frontier:
                for nbr in sorted(undirected.get(node, ())):
                    if nbr not in seen:
                        seen.add(nbr)
                        nxt.add(nbr)
                        out.append(nbr)
            frontier = nxt
            if not frontier:
                break
        return out

    async def ingest(self, source: Any, title: str | None = None) -> Sequence[str]:
        text = str(source)
        if self.gateway is not None:
            compiled = await self.gateway.complete(
                [{"role": "user", "content":
                    "Write a concise wiki page for this source. Start with a single '# Title' line, then "
                    "2-4 short sections. Link related concepts as [[wikilinks]]. Source:\n\n" + text}],
                max_tokens=900,
            )
        else:
            compiled = f"# {title or 'Note'}\n\n{text}"
        compiled = compiled.strip()
        heading = next((ln[2:].strip() for ln in compiled.splitlines() if ln.startswith("# ")), None)
        page_id = _slug(title or heading or " ".join(text.split()[:6]))
        # Slugs come from an uncontrolled model heading (or the first six words),
        # so collisions are cheap to produce ('# Overview'...) and an unconditional
        # write would silently destroy an existing page — including hand-curated
        # ones. Rule: re-ingesting the SAME source (matched by fingerprint in the
        # frontmatter) updates its page in place (idempotent); a DIFFERENT source
        # landing on an existing slug gets a -2/-3... suffix instead of overwriting.
        src_sha = hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:12]
        page_id = self._resolve_ingest_slug(page_id, src_sha)
        today = datetime.date.today().isoformat()
        front = (f"---\ntype: synthesis\ncreated: {today}\nupdated: {today}\n"
                 f"sources: [ingest]\nsource_sha: {src_sha}\n---\n\n")
        await self.write_page(page_id, front + compiled + "\n")
        self._append_log(f"ingested -> [[{page_id}]]")
        return [page_id]

    def _resolve_ingest_slug(self, base: str, src_sha: str) -> str:
        """First free slug for an ingest, reusing an existing page only when its
        frontmatter carries the same source fingerprint (same source re-ingested)."""
        candidate, n = base, 2
        while self._page_path(candidate).exists():
            existing = self._page_path(candidate).read_text(errors="replace")
            if f"source_sha: {src_sha}" in existing:
                return candidate  # same source: update in place is the intent
            candidate = f"{base}-{n}"
            n += 1
        return candidate

    async def lint(self) -> Sequence[Any]:
        pages = list(await self.list_pages())
        inbound: dict[str, int] = {p: 0 for p in pages}
        issues: list[dict[str, Any]] = []
        for p in pages:
            for link in await self.parse_links(p):
                target = link.split("|")[0].strip()  # strip [[page|alias]] display text
                slug = _slug(target)
                if slug in inbound:
                    inbound[slug] += 1
                else:
                    issues.append({"type": "dangling_link", "from": p, "to": target})
        for p, n in inbound.items():
            if n == 0:
                issues.append({"type": "orphan", "page": p})
        return issues

    async def _reindex(self) -> None:
        """Regenerate index.md as a TIERED Map-of-Content (hubs first, then by role),
        not a flat alphabetical list — so the vault reads as an organized map."""
        pages = sorted(p.stem for p in self.wiki.glob("*.md"))
        sections: dict[str, list[str]] = {}
        for p in pages:
            text = (self.wiki / f"{p}.md").read_text(encoding="utf-8", errors="replace")
            m = re.search(r"^type:\s*([A-Za-z_]+)", text, re.M)
            sections.setdefault(_section(p, m.group(1) if m else ""), []).append(p)
        out = ["# Wiki Index — Map of Content", "",
               "*Auto-generated by the kernel. Grouped by role; hubs first.*", ""]
        for sec in list(_SECTION_ORDER) + [s for s in sections if s not in _SECTION_ORDER]:
            if sections.get(sec):
                out.append(f"## {sec}")
                out += [f"- [[{p}]]" for p in sorted(sections[sec])]
                out.append("")
        index = self.root / "index.md"
        index.write_text("\n".join(out) + ("\n" if out else "*(empty)*\n"))
        secure_file(index, required=True)

    def _append_log(self, note: str) -> None:
        log = self.root / "log.md"
        stamp = datetime.date.today().isoformat()
        with log.open("a") as f:
            f.write(f"- {stamp} {note}\n")
