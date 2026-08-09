"""Backend worker for pending embedding metadata."""

from __future__ import annotations

import hashlib
from typing import Any

from kernel.core.config import Config
from models.factory import build_gateway
from services.embedding_repository import EmbeddingRepository
from services.memory_repository import MemoryRepository


class EmbeddingWorker:
    def __init__(
        self,
        embedding_repository: EmbeddingRepository | None = None,
        memory_repository: MemoryRepository | None = None,
        provider: Any | None = None,
    ) -> None:
        self.embedding_repository = embedding_repository or EmbeddingRepository()
        self.memory_repository = memory_repository or MemoryRepository()
        self.provider = provider or build_gateway(Config())

    async def process_pending_memory_embeddings(self, user_id: str, limit: int = 25) -> list[dict[str, Any]]:
        pending = self.embedding_repository.list_pending_memory_embeddings(user_id, limit=limit)
        results = []
        for item in pending:
            results.append(await self.process_embedding(user_id, item["id"]))
        return results

    async def process_embedding(self, user_id: str, embedding_id: str) -> dict[str, Any] | None:
        item = self.embedding_repository.get_memory_embedding(user_id, embedding_id)
        if not item:
            return None
        if item.get("status") != "pending":
            return item

        memory = self.memory_repository.get_memory(user_id, str(item.get("sourceId") or ""))
        if not memory or memory.get("status") != "active":
            return self.mark_failed(user_id, embedding_id, "missing_source")

        try:
            vectors = await self.provider.embed([str(memory.get("text") or "")], model=item.get("model"))
        except Exception as exc:
            return self.mark_failed(user_id, embedding_id, _error_code(exc))

        vector = list(vectors[0]) if vectors else []
        dimensions = len(vector) or int(item.get("dimensions") or 0)
        vector_ref = _vector_ref(user_id, embedding_id, vector)
        return self.mark_embedded(user_id, embedding_id, vector_ref, dimensions)

    def mark_embedded(self, user_id: str, embedding_id: str, vector_ref: str | None, dimensions: int) -> dict[str, Any] | None:
        return self.embedding_repository.mark_embedded(user_id, embedding_id, vector_ref, dimensions)

    def mark_failed(self, user_id: str, embedding_id: str, error_code: str) -> dict[str, Any] | None:
        return self.embedding_repository.mark_failed(user_id, embedding_id, error_code)


def _vector_ref(user_id: str, embedding_id: str, vector: list[float]) -> str:
    digest = hashlib.sha256(",".join(str(x) for x in vector[:16]).encode("utf-8")).hexdigest()[:24]
    return f"vectors/users/{user_id}/memoryEmbeddings/{embedding_id}/{digest}"


def _error_code(exc: Exception) -> str:
    name = type(exc).__name__.replace("Error", "").replace("Exception", "")
    return f"provider_{name or 'failure'}"
