"""Firestore client boundary.

Production uses Google Firestore through the optional `google-cloud-firestore`
package. Tests can select the in-memory backend with
`ENGRAM_FIRESTORE_BACKEND=memory` while preserving Firestore document paths.
"""

from __future__ import annotations

import copy
import os
from typing import Any, Protocol


class FirestoreClient(Protocol):
    def get_document(self, path: str) -> dict[str, Any] | None:
        ...

    def set_document(self, path: str, data: dict[str, Any]) -> None:
        ...

    def delete_document(self, path: str) -> None:
        ...

    def list_documents(self, collection_path: str, *, limit: int = 50) -> list[dict[str, Any]]:
        ...


_MEMORY_STORES: dict[str, dict[str, dict[str, Any]]] = {}
_CLIENTS: dict[str, FirestoreClient] = {}


def get_firestore_client() -> FirestoreClient:
    backend = os.environ.get("ENGRAM_FIRESTORE_BACKEND", "auto").strip().lower() or "auto"
    namespace = os.environ.get("ENGRAM_FIRESTORE_NAMESPACE", "default").strip() or "default"
    key = f"{backend}:{namespace}"
    if key in _CLIENTS:
        return _CLIENTS[key]

    if backend == "memory":
        client: FirestoreClient = MemoryFirestoreClient(_MEMORY_STORES.setdefault(namespace, {}))
    elif backend in {"google", "auto"}:
        try:
            client = GoogleFirestoreClient()
        except Exception:
            if backend == "google":
                raise
            client = MemoryFirestoreClient(_MEMORY_STORES.setdefault(namespace, {}))
    else:
        raise RuntimeError("ENGRAM_FIRESTORE_BACKEND must be auto, google, or memory")

    _CLIENTS[key] = client
    return client


class MemoryFirestoreClient:
    def __init__(self, store: dict[str, dict[str, Any]]) -> None:
        self.store = store

    def get_document(self, path: str) -> dict[str, Any] | None:
        item = self.store.get(_clean_path(path))
        return copy.deepcopy(item) if item is not None else None

    def set_document(self, path: str, data: dict[str, Any]) -> None:
        self.store[_clean_path(path)] = copy.deepcopy(data)

    def delete_document(self, path: str) -> None:
        self.store.pop(_clean_path(path), None)

    def list_documents(self, collection_path: str, *, limit: int = 50) -> list[dict[str, Any]]:
        prefix = _clean_path(collection_path) + "/"
        rows = []
        for path, data in self.store.items():
            if path.startswith(prefix) and "/" not in path[len(prefix):]:
                rows.append(copy.deepcopy(data))
        rows.sort(key=lambda item: str(item.get("createdAt", "")))
        return rows[:limit]


class GoogleFirestoreClient:
    def __init__(self) -> None:
        from google.cloud import firestore  # type: ignore

        project = os.environ.get("ENGRAM_FIRESTORE_PROJECT_ID") or os.environ.get("GOOGLE_CLOUD_PROJECT")
        self.client = firestore.Client(project=project)

    def get_document(self, path: str) -> dict[str, Any] | None:
        snapshot = self.client.document(_clean_path(path)).get()
        return snapshot.to_dict() if snapshot.exists else None

    def set_document(self, path: str, data: dict[str, Any]) -> None:
        self.client.document(_clean_path(path)).set(data)

    def delete_document(self, path: str) -> None:
        self.client.document(_clean_path(path)).delete()

    def list_documents(self, collection_path: str, *, limit: int = 50) -> list[dict[str, Any]]:
        stream = (
            self.client.collection(_clean_path(collection_path))
            .order_by("createdAt")
            .limit(limit)
            .stream()
        )
        return [doc.to_dict() for doc in stream]


def _clean_path(path: str) -> str:
    cleaned = path.strip().strip("/")
    if not cleaned:
        raise ValueError("Firestore path is required")
    return cleaned
