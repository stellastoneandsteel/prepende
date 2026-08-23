"""MemoryStore — the one shared, persistent, growing memory.

All agents read from and write to this. It survives across sessions,
accumulates, and gets more useful over time. Scoping (who can read/write
what) is enforced at the substrate via RLS, not assumed by callers.

Day-one impl: memory/  over Postgres (pgvector + Apache AGE).
Swap targets behind this same interface: Mem0 (Apache-2.0), Cognee
(Apache-2.0), or a dedicated vector DB (Qdrant) if pgvector is outgrown.

SKELETON — signatures only, no implementation yet.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Sequence


class MemoryStore(ABC):
    """write / search / consolidate / link, all scope-aware."""

    @abstractmethod
    async def write(self, content: str, *, scope: str, metadata: dict[str, Any] | None = None) -> str:
        """Persist a memory within a scope. Returns its id."""

    @abstractmethod
    async def search(self, query: str, *, scope: str, k: int = 10) -> Sequence[Any]:
        """Semantic recall within a scope."""

    @abstractmethod
    async def update(
        self,
        memory_id: str,
        *,
        scope: str,
        content: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Any | None:
        """Update a memory within a scope. Returns the updated memory, or None."""

    @abstractmethod
    async def delete(self, memory_id: str, *, scope: str) -> bool:
        """Soft-delete a memory within a scope."""

    @abstractmethod
    async def consolidate(self, *, scope: str, **opts: Any) -> Any:
        """Dedupe/merge/summarize accumulated memories — how it gets *more* useful,
        not just bigger. Implementations may return a report (callers may ignore it)
        and accept tuning opts (e.g. `sim_threshold`, `summarizer`)."""

    @abstractmethod
    async def link(self, src_id: str, dst_id: str, *, relation: str) -> None:
        """Record a relationship edge (entity/relationship graph, Apache AGE)."""
