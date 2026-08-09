"""Firestore-backed conversation repository."""

from __future__ import annotations

import time
import uuid
from typing import Any

from services.firestore_client import FirestoreClient, get_firestore_client


class ConversationRepository:
    def __init__(self, client: FirestoreClient | None = None) -> None:
        self.client = client or get_firestore_client()

    def create_conversation(self, user_id: str, data: dict[str, Any]) -> dict[str, Any]:
        conversation_id = str(data.get("id") or f"conv_{uuid.uuid4().hex[:16]}")
        _validate_conversation_id(conversation_id)
        now = _now()
        conversation = {
            "id": conversation_id,
            "userId": user_id,
            "title": str(data.get("title") or "New conversation").strip()[:120] or "New conversation",
            "status": str(data.get("status") or "active"),
            "modelProvider": str(data.get("modelProvider") or ""),
            "modelName": str(data.get("modelName") or ""),
            "createdAt": data.get("createdAt") or now,
            "updatedAt": data.get("updatedAt") or now,
            "memoryHooks": data.get("memoryHooks") or {"extractionPending": False, "implemented": False},
        }
        self.client.set_document(_conversation_path(user_id, conversation_id), conversation)
        return conversation

    def get_conversation(self, user_id: str, conversation_id: str) -> dict[str, Any] | None:
        _validate_conversation_id(conversation_id)
        conversation = self.client.get_document(_conversation_path(user_id, conversation_id))
        if not conversation or conversation.get("userId") != user_id:
            return None
        return conversation

    def list_conversations(self, user_id: str, limit: int = 50) -> list[dict[str, Any]]:
        conversations = [
            item for item in self.client.list_documents(_conversations_path(user_id), limit=limit)
            if item.get("userId") == user_id and item.get("status") != "deleted"
        ]
        return sorted(
            conversations,
            key=lambda item: str(item.get("updatedAt") or item.get("createdAt") or ""),
            reverse=True,
        )[:limit]

    def append_message(self, user_id: str, conversation_id: str, data: dict[str, Any]) -> dict[str, Any]:
        conversation = self.get_conversation(user_id, conversation_id)
        if not conversation:
            raise KeyError("conversation not found")

        message_id = str(data.get("id") or f"msg_{uuid.uuid4().hex[:16]}")
        message = {
            "id": message_id,
            "userId": user_id,
            "conversationId": conversation_id,
            "role": str(data["role"]),
            "content": str(data["content"]),
            "createdAt": data.get("createdAt") or _now(),
        }
        if data.get("modelProvider"):
            message["modelProvider"] = str(data["modelProvider"])
        if data.get("modelName"):
            message["modelName"] = str(data["modelName"])

        self.client.set_document(_message_path(user_id, conversation_id, message_id), message)
        conversation["updatedAt"] = message["createdAt"]
        if message.get("modelProvider"):
            conversation["modelProvider"] = message["modelProvider"]
        if message.get("modelName"):
            conversation["modelName"] = message["modelName"]
        if data.get("memoryHooks"):
            conversation["memoryHooks"] = data["memoryHooks"]
        self.client.set_document(_conversation_path(user_id, conversation_id), conversation)
        return message

    def list_messages(self, user_id: str, conversation_id: str, limit: int = 50) -> list[dict[str, Any]]:
        conversation = self.get_conversation(user_id, conversation_id)
        if not conversation or conversation.get("status") == "deleted":
            raise KeyError("conversation not found")
        return self.client.list_documents(_messages_path(user_id, conversation_id), limit=limit)

    def update_conversation(self, user_id: str, conversation_id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
        conversation = self.get_conversation(user_id, conversation_id)
        if not conversation or conversation.get("status") == "deleted":
            return None
        if "title" in patch:
            conversation["title"] = str(patch.get("title") or "").strip()[:120] or conversation["title"]
        if "status" in patch:
            status = str(patch.get("status") or "").strip()
            if status in {"active", "archived"}:
                conversation["status"] = status
        conversation["updatedAt"] = _now()
        self.client.set_document(_conversation_path(user_id, conversation_id), conversation)
        return conversation

    def delete_conversation(self, user_id: str, conversation_id: str) -> bool:
        conversation = self.get_conversation(user_id, conversation_id)
        if not conversation or conversation.get("status") == "deleted":
            return False
        now = _now()
        conversation["status"] = "deleted"
        conversation["deletedAt"] = now
        conversation["updatedAt"] = now
        self.client.set_document(_conversation_path(user_id, conversation_id), conversation)
        return True


def _conversations_path(user_id: str) -> str:
    return f"users/{user_id}/conversations"


def _conversation_path(user_id: str, conversation_id: str) -> str:
    return f"{_conversations_path(user_id)}/{conversation_id}"


def _messages_path(user_id: str, conversation_id: str) -> str:
    return f"{_conversation_path(user_id, conversation_id)}/messages"


def _message_path(user_id: str, conversation_id: str, message_id: str) -> str:
    return f"{_messages_path(user_id, conversation_id)}/{message_id}"


def _validate_conversation_id(conversation_id: str) -> None:
    if not conversation_id.startswith("conv_") or "/" in conversation_id:
        raise ValueError("invalid conversation id")


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
