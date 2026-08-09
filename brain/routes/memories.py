"""Memory review route handlers."""

from __future__ import annotations

from typing import Any, Mapping

from services.auth_context import AuthError, require_auth
from services.memory_repository import MemoryRepository


def handle_get(path: str, headers: Mapping[str, Any]) -> tuple[int, dict[str, Any]]:
    try:
        auth = require_auth(headers)
        repository = MemoryRepository()
        if path == "/memories/pending":
            return 200, {"pendingMemories": [_public_memory(item) for item in repository.list_pending_memories(auth.user_id)]}
        if path == "/memories":
            return 200, {"memories": [_public_memory(item) for item in repository.list_memories(auth.user_id)]}
        return 404, {"error": "not found"}
    except AuthError as exc:
        return exc.status, {"error": str(exc)}


def handle_post(path: str, data: dict[str, Any], headers: Mapping[str, Any]) -> tuple[int, dict[str, Any]]:
    try:
        auth = require_auth(headers)
        repository = MemoryRepository()
        prefix = "/memories/pending/"
        if path.startswith(prefix) and path.endswith("/approve"):
            pending_id = path[len(prefix):-len("/approve")].strip("/")
            memory = repository.approve_pending_memory(auth.user_id, pending_id)
            if not memory:
                return 404, {"error": "pending memory not found"}
            return 200, {"memory": _public_memory(memory)}
        if path.startswith(prefix) and path.endswith("/reject"):
            pending_id = path[len(prefix):-len("/reject")].strip("/")
            pending = repository.reject_pending_memory(auth.user_id, pending_id)
            if not pending:
                return 404, {"error": "pending memory not found"}
            return 200, {"pendingMemory": _public_memory(pending)}
        return 404, {"error": "not found"}
    except AuthError as exc:
        return exc.status, {"error": str(exc)}
    except ValueError as exc:
        return 400, {"error": str(exc)}


def handle_delete(path: str, headers: Mapping[str, Any]) -> tuple[int, dict[str, Any]]:
    try:
        auth = require_auth(headers)
        repository = MemoryRepository()
        prefix = "/memories/"
        if path.startswith(prefix) and path != "/memories/pending":
            memory_id = path[len(prefix):].strip("/")
            if repository.delete_memory(auth.user_id, memory_id):
                return 200, {"deleted": True, "id": memory_id}
            return 404, {"error": "memory not found"}
        return 404, {"error": "not found"}
    except AuthError as exc:
        return exc.status, {"error": str(exc)}
    except ValueError as exc:
        return 400, {"error": str(exc)}


def _public_memory(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item["id"],
        "source": item.get("source", {}),
        "kind": item.get("kind", "fact"),
        "text": item.get("text", ""),
        "confidence": item.get("confidence", 0.0),
        "status": item.get("status", ""),
        "createdAt": item.get("createdAt", ""),
        "createdBy": item.get("createdBy", ""),
        "embeddingMetadata": _public_embedding(item.get("embeddingMetadata")),
    }


def _public_embedding(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    return {
        "id": item.get("id", ""),
        "sourceType": item.get("sourceType", ""),
        "sourceId": item.get("sourceId", ""),
        "model": item.get("model", ""),
        "dimensions": item.get("dimensions", 0),
        "textHash": item.get("textHash", ""),
        "vectorRef": item.get("vectorRef"),
        "status": item.get("status", ""),
        "createdAt": item.get("createdAt", ""),
        "updatedAt": item.get("updatedAt", ""),
    }
