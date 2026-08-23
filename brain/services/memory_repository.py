"""Firestore-backed memory repository with pending review."""

from __future__ import annotations

import time
import uuid
from typing import Any

from services.embedding_service import EmbeddingService
from services.firestore_client import FirestoreClient, get_firestore_client


class MemoryRepository:
    def __init__(self, client: FirestoreClient | None = None, embedding_service: EmbeddingService | None = None) -> None:
        self.client = client or get_firestore_client()
        self.embedding_service = embedding_service or EmbeddingService()

    def create_pending_memory(self, user_id: str, data: dict[str, Any]) -> dict[str, Any]:
        pending_id = str(data.get("id") or f"pmem_{uuid.uuid4().hex[:16]}")
        _validate_id(pending_id, "pmem_")
        pending = {
            "id": pending_id,
            "userId": user_id,
            "source": data["source"],
            "kind": _normalize_kind(str(data.get("kind") or "fact")),
            "text": str(data["text"]).strip(),
            "confidence": float(data.get("confidence") or 0.0),
            "status": "pending",
            "createdAt": data.get("createdAt") or _now(),
            "createdBy": "system",
        }
        self.client.set_document(_pending_path(user_id, pending_id), pending)
        return pending

    def list_pending_memories(self, user_id: str, limit: int = 50) -> list[dict[str, Any]]:
        return [
            item for item in self.client.list_documents(_pending_collection(user_id), limit=limit)
            if item.get("userId") == user_id and item.get("status") == "pending"
        ]

    def get_pending_memory(self, user_id: str, pending_id: str) -> dict[str, Any] | None:
        _validate_id(pending_id, "pmem_")
        item = self.client.get_document(_pending_path(user_id, pending_id))
        if not item or item.get("userId") != user_id:
            return None
        return item

    def approve_pending_memory(self, user_id: str, pending_id: str) -> dict[str, Any] | None:
        pending = self.get_pending_memory(user_id, pending_id)
        if not pending or pending.get("status") != "pending":
            return None
        memory_id = f"mem_{uuid.uuid4().hex[:16]}"
        now = _now()
        memory = {
            "id": memory_id,
            "userId": user_id,
            "source": pending["source"],
            "kind": pending["kind"],
            "text": pending["text"],
            "confidence": pending["confidence"],
            "status": "active",
            "createdAt": now,
            "updatedAt": now,
            "createdBy": "user",
            "pendingMemoryId": pending_id,
        }
        pending["status"] = "approved"
        pending["reviewedAt"] = now
        self.client.set_document(_memory_path(user_id, memory_id), memory)
        self.client.set_document(_pending_path(user_id, pending_id), pending)
        memory["embeddingMetadata"] = self.embedding_service.queue_memory_embedding(user_id, memory)
        return memory

    def reject_pending_memory(self, user_id: str, pending_id: str) -> dict[str, Any] | None:
        pending = self.get_pending_memory(user_id, pending_id)
        if not pending or pending.get("status") != "pending":
            return None
        pending["status"] = "rejected"
        pending["reviewedAt"] = _now()
        self.client.set_document(_pending_path(user_id, pending_id), pending)
        return pending

    def list_memories(self, user_id: str, limit: int = 50) -> list[dict[str, Any]]:
        return [
            item for item in self.client.list_documents(_memory_collection(user_id), limit=limit)
            if item.get("userId") == user_id and item.get("status") == "active"
        ]

    def get_memory(self, user_id: str, memory_id: str) -> dict[str, Any] | None:
        _validate_id(memory_id, "mem_")
        memory = self.client.get_document(_memory_path(user_id, memory_id))
        if not memory or memory.get("userId") != user_id:
            return None
        return memory

    def delete_memory(self, user_id: str, memory_id: str) -> bool:
        _validate_id(memory_id, "mem_")
        memory = self.get_memory(user_id, memory_id)
        if not memory:
            return False
        memory["status"] = "deleted"
        memory["deletedAt"] = _now()
        memory["updatedAt"] = memory["deletedAt"]
        self.client.set_document(_memory_path(user_id, memory_id), memory)
        self.embedding_service.mark_memory_deleted(user_id, memory_id)
        return True


def _pending_collection(user_id: str) -> str:
    return f"users/{user_id}/pendingMemories"


def _memory_collection(user_id: str) -> str:
    return f"users/{user_id}/memories"


def _pending_path(user_id: str, pending_id: str) -> str:
    return f"{_pending_collection(user_id)}/{pending_id}"


def _memory_path(user_id: str, memory_id: str) -> str:
    return f"{_memory_collection(user_id)}/{memory_id}"


def _validate_id(value: str, prefix: str) -> None:
    if not value.startswith(prefix) or "/" in value:
        raise ValueError("invalid memory id")


def _normalize_kind(kind: str) -> str:
    allowed = {"preference", "fact", "goal", "instruction", "profile"}
    return kind if kind in allowed else "fact"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
