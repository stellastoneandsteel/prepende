"""Knowledge — the self-organizing wiki, with the vault as source of truth.

The Obsidian vault (plain markdown + git) is the durable, human-readable
master. The RAG index is a disposable projection rebuilt from it. The kernel
reads/writes the vault files directly — NO dependency on the Obsidian app.

Self-organization is scheduled agent passes (ingest → link → summarize →
lint), per the Karpathy "LLM wiki" pattern, run through the ModelGateway.

Impl: knowledge/  over vault/
SKELETON — signatures only, no implementation yet.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Sequence


class Knowledge(ABC):
    """Read/write the vault and parse it into a graph. Vault is canonical."""

    @abstractmethod
    async def read_page(self, page_id: str) -> str:
        """Read a wiki page (markdown + YAML frontmatter)."""

    @abstractmethod
    async def write_page(self, page_id: str, content: str) -> None:
        """Create/update a wiki page. The vault is the source of truth."""

    @abstractmethod
    async def parse_links(self, page_id: str) -> Sequence[str]:
        """Extract [[wikilinks]] from a page — edges of the knowledge graph."""

    @abstractmethod
    async def ingest(self, source: Any) -> Sequence[str]:
        """Compile a raw/ source into wiki/ pages. Returns affected page ids."""

    @abstractmethod
    async def lint(self) -> Sequence[Any]:
        """Health check: contradictions, stale claims, orphans, gaps. Self-healing trigger."""
