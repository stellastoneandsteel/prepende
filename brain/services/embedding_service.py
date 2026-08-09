"""Embedding metadata queueing boundary.

This slice records embedding work to be done later. It does not call an
embedding provider or store vectors yet.
"""

from __future__ import annotations

import hashlib
import os
from typing import Any

from services.embedding_repository import EmbeddingRepository
from prepende_brain.env import brand_env


class EmbeddingService:
    def __init__(self, repository: EmbeddingRepository | None = None) -> None:
        self.repository = repository or EmbeddingRepository()
        self.model = (
            os.environ.get("EMBEDDING_MODEL", "")
            or brand_env("EMBEDDING_MODEL")
            or "text-embedding-3-small"
        ).strip() or "text-embedding-3-small"
        self.dimensions = int(
            (os.environ.get("EMBEDDING_DIM", "") or brand_env("EMBEDDING_DIMENSIONS") or "1536")
            or 1536
        )

    def queue_memory_embedding(self, user_id: str, memory: dict[str, Any]) -> dict[str, Any]:
        return self.repository.create_memory_embedding(user_id, {
            "sourceId": memory["id"],
            "model": self.model,
            "dimensions": self.dimensions,
            "textHash": _text_hash(str(memory.get("text") or "")),
            "vectorRef": None,
            "status": "pending",
        })

    def mark_memory_deleted(self, user_id: str, memory_id: str) -> list[dict[str, Any]]:
        return self.repository.mark_memory_embeddings_deleted(user_id, memory_id)


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
