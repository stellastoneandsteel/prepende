"""Build provider-ready context from approved user memory and conversation history."""

from __future__ import annotations

from typing import Any

from services.conversation_repository import ConversationRepository
from services.memory_repository import MemoryRepository


class ContextBuilderService:
    def __init__(
        self,
        conversation_repository: ConversationRepository | None = None,
        memory_repository: MemoryRepository | None = None,
    ) -> None:
        self.conversation_repository = conversation_repository or ConversationRepository()
        self.memory_repository = memory_repository or MemoryRepository()

    def get_relevant_memories(self, user_id: str, limit: int = 20) -> list[dict[str, Any]]:
        memories = self.memory_repository.list_memories(user_id, limit=limit)
        return sorted(
            memories,
            key=lambda memory: str(memory.get("updatedAt") or memory.get("createdAt") or ""),
            reverse=True,
        )[:limit]

    def build_messages(
        self,
        user_id: str,
        conversation_id: str,
        new_user_message: dict[str, Any],
    ) -> list[dict[str, str]]:
        history_rows = self.conversation_repository.list_messages(user_id, conversation_id, limit=50)
        history = [
            {"role": msg["role"], "content": msg["content"]}
            for msg in history_rows
            if msg.get("role") in {"user", "assistant", "system"}
        ]
        if not any(msg.get("role") == "user" and msg.get("id") == new_user_message.get("id") for msg in history_rows):
            history.append({"role": "user", "content": str(new_user_message.get("content") or "")})

        messages = [{
            "role": "system",
            "content": (
                "Engram should use approved memories when helpful. "
                "Do not reveal hidden memory mechanics. "
                "Do not treat pending memories as approved facts."
            ),
        }]
        memory_context = self._memory_context(self.get_relevant_memories(user_id))
        if memory_context:
            messages.append({"role": "system", "content": memory_context})
        messages.extend(history)
        return messages

    def _memory_context(self, memories: list[dict[str, Any]]) -> str:
        if not memories:
            return ""
        lines = ["Approved user memories:"]
        for memory in memories:
            kind = str(memory.get("kind") or "fact")
            text = " ".join(str(memory.get("text") or "").split())
            if text:
                lines.append(f"- {kind}: {text}")
        return "\n".join(lines)
