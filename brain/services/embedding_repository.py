"""Firestore-backed embedding metadata repository."""

from __future__ import annotations

import time
import uuid
from typing import Any

from services.firestore_client import FirestoreClient, get_firestore_client


class EmbeddingRepository:
    def __init__(self, client: FirestoreClient | None = None) -> None:
        self.client = client or get_firestore_client()

    def create_memory_embedding(self, user_id: str, data: dict[str, Any]) -> dict[str, Any]:
        embedding_id = str(data.get("id") or f"emb_{uuid.uuid4().hex[:16]}")
        _validate_embedding_id(embedding_id)
        item = _embedding_record(user_id, embedding_id, "memory", data)
        self.client.set_document(_memory_embedding_path(user_id, embedding_id), item)
        return item

    def create_knowledge_embedding(self, user_id: str, data: dict[str, Any]) -> dict[str, Any]:
        embedding_id = str(data.get("id") or f"emb_{uuid.uuid4().hex[:16]}")
        _validate_embedding_id(embedding_id)
        item = _embedding_record(user_id, embedding_id, "knowledge", data)
        self.client.set_document(_knowledge_embedding_path(user_id, embedding_id), item)
        return item

    def list_memory_embeddings(self, user_id: str, limit: int = 50) -> list[dict[str, Any]]:
        return [
            item for item in self.client.list_documents(_memory_embeddings_path(user_id), limit=limit)
            if item.get("userId") == user_id
        ]

    def list_pending_memory_embeddings(self, user_id: str, limit: int = 25) -> list[dict[str, Any]]:
        return [
            item for item in self.list_memory_embeddings(user_id, limit=limit)
            if item.get("status") == "pending"
        ][:limit]

    def get_memory_embedding(self, user_id: str, embedding_id: str) -> dict[str, Any] | None:
        _validate_embedding_id(embedding_id)
        item = self.client.get_document(_memory_embedding_path(user_id, embedding_id))
        if not item or item.get("userId") != user_id:
            return None
        return item

    def list_knowledge_embeddings(self, user_id: str, limit: int = 50) -> list[dict[str, Any]]:
        return [
            item for item in self.client.list_documents(_knowledge_embeddings_path(user_id), limit=limit)
            if item.get("userId") == user_id
        ]

    def list_memory_embeddings_for_source(self, user_id: str, memory_id: str, limit: int = 20) -> list[dict[str, Any]]:
        return [
            item for item in self.list_memory_embeddings(user_id, limit=limit)
            if item.get("sourceType") == "memory" and item.get("sourceId") == memory_id
        ]

    def mark_memory_embeddings_deleted(self, user_id: str, memory_id: str) -> list[dict[str, Any]]:
        updated = []
        now = _now()
        for item in self.list_memory_embeddings_for_source(user_id, memory_id):
            item["status"] = "deleted"
            item["updatedAt"] = now
            self.client.set_document(_memory_embedding_path(user_id, item["id"]), item)
            updated.append(item)
        return updated

    def mark_embedded(self, user_id: str, embedding_id: str, vector_ref: str | None, dimensions: int) -> dict[str, Any] | None:
        item = self.get_memory_embedding(user_id, embedding_id)
        if not item:
            return None
        item["status"] = "embedded"
        item["vectorRef"] = vector_ref
        item["dimensions"] = int(dimensions)
        item["updatedAt"] = _now()
        item.pop("error", None)
        self.client.set_document(_memory_embedding_path(user_id, embedding_id), item)
        return item

    def mark_failed(self, user_id: str, embedding_id: str, error_code: str) -> dict[str, Any] | None:
        item = self.get_memory_embedding(user_id, embedding_id)
        if not item:
            return None
        item["status"] = "failed"
        item["error"] = {"code": _safe_error_code(error_code)}
        item["updatedAt"] = _now()
        self.client.set_document(_memory_embedding_path(user_id, embedding_id), item)
        return item


def _embedding_record(user_id: str, embedding_id: str, source_type: str, data: dict[str, Any]) -> dict[str, Any]:
    now = _now()
    return {
        "id": embedding_id,
        "userId": user_id,
        "sourceType": source_type,
        "sourceId": str(data["sourceId"]),
        "model": str(data["model"]),
        "dimensions": int(data["dimensions"]),
        "textHash": str(data["textHash"]),
        "vectorRef": data.get("vectorRef"),
        "status": str(data.get("status") or "pending"),
        "createdAt": data.get("createdAt") or now,
        "updatedAt": data.get("updatedAt") or now,
    }


def _memory_embeddings_path(user_id: str) -> str:
    return f"users/{user_id}/memoryEmbeddings"


def _knowledge_embeddings_path(user_id: str) -> str:
    return f"users/{user_id}/knowledgeEmbeddings"


def _memory_embedding_path(user_id: str, embedding_id: str) -> str:
    return f"{_memory_embeddings_path(user_id)}/{embedding_id}"


def _knowledge_embedding_path(user_id: str, embedding_id: str) -> str:
    return f"{_knowledge_embeddings_path(user_id)}/{embedding_id}"


def _validate_embedding_id(embedding_id: str) -> None:
    if not embedding_id.startswith("emb_") or "/" in embedding_id:
        raise ValueError("invalid embedding id")


def _safe_error_code(value: str) -> str:
    allowed = "".join(ch for ch in value.lower().replace(" ", "_") if ch.isalnum() or ch == "_")
    return allowed[:80] or "embedding_failed"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
