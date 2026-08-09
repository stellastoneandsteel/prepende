"""Authenticated conversation persistence and provider orchestration."""

from __future__ import annotations

import asyncio
from typing import Any

from services.auth_context import AuthContext
from services.context_builder_service import ContextBuilderService
from services.conversation_repository import ConversationRepository
from services.memory_extraction_service import MemoryExtractionService
from services.provider_service import ProviderService


class ConversationError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


class ConversationService:
    def __init__(
        self,
        repository: ConversationRepository | None = None,
        provider: ProviderService | None = None,
        memory_extractor: MemoryExtractionService | None = None,
        context_builder: ContextBuilderService | None = None,
    ) -> None:
        self.repository = repository or ConversationRepository()
        self.provider = provider or ProviderService()
        self.memory_extractor = memory_extractor or MemoryExtractionService()
        self.context_builder = context_builder or ContextBuilderService(
            conversation_repository=self.repository,
        )

    def create_conversation(self, auth: AuthContext, data: dict[str, Any]) -> dict[str, Any]:
        conversation = self.repository.create_conversation(auth.user_id, {
            "title": data.get("title"),
            "modelProvider": self.provider.provider_name,
            "modelName": self.provider.model_name,
            "memoryHooks": {"extractionPending": False, "implemented": False},
        })
        return _public_conversation(conversation)

    def list_conversations(self, auth: AuthContext, limit: int = 50) -> list[dict[str, Any]]:
        return [_public_conversation(item) for item in self.repository.list_conversations(auth.user_id, limit=limit)]

    def get_conversation(self, auth: AuthContext, conversation_id: str) -> dict[str, Any]:
        return _public_conversation(self._get_owned_conversation(auth.user_id, conversation_id))

    def list_messages(self, auth: AuthContext, conversation_id: str, limit: int = 100) -> list[dict[str, Any]]:
        try:
            messages = self.repository.list_messages(auth.user_id, conversation_id, limit=limit)
        except KeyError as exc:
            raise ConversationError("conversation not found", 404) from exc
        return [_public_message(message) for message in messages]

    def update_conversation(self, auth: AuthContext, conversation_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        try:
            conversation = self.repository.update_conversation(auth.user_id, conversation_id, patch)
        except ValueError as exc:
            raise ConversationError(str(exc), 400) from exc
        if not conversation:
            raise ConversationError("conversation not found", 404)
        return _public_conversation(conversation)

    def delete_conversation(self, auth: AuthContext, conversation_id: str) -> dict[str, Any]:
        try:
            deleted = self.repository.delete_conversation(auth.user_id, conversation_id)
        except ValueError as exc:
            raise ConversationError(str(exc), 400) from exc
        if not deleted:
            raise ConversationError("conversation not found", 404)
        return {"deleted": True, "id": conversation_id}

    def add_user_message(self, auth: AuthContext, conversation_id: str, data: dict[str, Any]) -> dict[str, Any]:
        content = str(data.get("message") or data.get("content") or "").strip()
        if not content:
            raise ConversationError("message is required")

        conversation = self._get_owned_conversation(auth.user_id, conversation_id)
        user_message = self.repository.append_message(auth.user_id, conversation_id, {
            "role": "user",
            "content": content,
        })

        provider_messages = self.context_builder.build_messages(
            auth.user_id,
            conversation_id,
            user_message,
        )
        assistant_text = asyncio.run(
            self.provider.complete_conversation(
                provider_messages,
                user_id=auth.user_id,
                conversation_id=conversation_id,
            )
        )

        assistant_message = self.repository.append_message(auth.user_id, conversation_id, {
            "role": "assistant",
            "content": assistant_text,
            "modelProvider": self.provider.provider_name,
            "modelName": self.provider.model_name,
            "memoryHooks": {"extractionPending": True, "implemented": False},
        })
        pending_memories = self.memory_extractor.extract_from_turn(
            auth.user_id,
            conversation_id,
            user_message,
            assistant_message,
        )
        conversation = self._get_owned_conversation(auth.user_id, conversation_id)
        memory_hooks = {
            "extractionPending": False,
            "implemented": True,
            "pendingReviewCount": len(pending_memories),
            "pendingMemoryIds": [memory["id"] for memory in pending_memories],
        }

        return {
            "conversation": _public_conversation(conversation),
            "userMessage": _public_message(user_message),
            "assistantMessage": _public_message(assistant_message),
            "memoryHooks": memory_hooks,
        }

    def _get_owned_conversation(self, user_id: str, conversation_id: str) -> dict[str, Any]:
        try:
            conversation = self.repository.get_conversation(user_id, conversation_id)
        except ValueError as exc:
            raise ConversationError(str(exc), 400) from exc
        if not conversation:
            raise ConversationError("conversation not found", 404)
        if conversation.get("status") == "deleted":
            raise ConversationError("conversation not found", 404)
        return conversation


def _public_conversation(conversation: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": conversation["id"],
        "title": conversation["title"],
        "status": conversation["status"],
        "modelProvider": conversation.get("modelProvider", ""),
        "modelName": conversation.get("modelName", ""),
        "createdAt": conversation["createdAt"],
        "updatedAt": conversation["updatedAt"],
    }


def _public_message(message: dict[str, Any]) -> dict[str, Any]:
    out = {
        "id": message["id"],
        "conversationId": message["conversationId"],
        "role": message["role"],
        "content": message["content"],
        "createdAt": message["createdAt"],
    }
    if message.get("role") == "assistant":
        out["modelProvider"] = message.get("modelProvider", "")
        out["modelName"] = message.get("modelName", "")
    return out
