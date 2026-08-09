"""Extract pending memory candidates from conversation turns."""

from __future__ import annotations

import re
from typing import Any

from services.memory_repository import MemoryRepository


class MemoryExtractionService:
    def __init__(self, repository: MemoryRepository | None = None) -> None:
        self.repository = repository or MemoryRepository()

    def extract_from_turn(
        self,
        user_id: str,
        conversation_id: str,
        user_message: dict[str, Any],
        assistant_message: dict[str, Any],
    ) -> list[dict[str, Any]]:
        candidate = _candidate_from_text(str(user_message.get("content") or ""))
        if not candidate:
            return []

        pending = self.repository.create_pending_memory(user_id, {
            "source": {
                "type": "conversation",
                "conversationId": conversation_id,
                "messageIds": [user_message["id"], assistant_message["id"]],
            },
            "kind": candidate["kind"],
            "text": candidate["text"],
            "confidence": candidate["confidence"],
        })
        return [pending]


def _candidate_from_text(text: str) -> dict[str, Any] | None:
    cleaned = " ".join(text.strip().split())
    if len(cleaned) < 8:
        return None

    lower = cleaned.lower()
    patterns = (
        r"^remember(?: that)? (?P<value>.+)$",
        r"^please remember(?: that)? (?P<value>.+)$",
        r"^(?P<value>my .+)$",
        r"^(?P<value>i prefer .+)$",
        r"^(?P<value>i like .+)$",
        r"^(?P<value>i am .+)$",
        r"^(?P<value>i'm .+)$",
        r"^(?P<value>my goal is .+)$",
    )
    extracted = None
    for pattern in patterns:
        match = re.match(pattern, cleaned, flags=re.IGNORECASE)
        if match:
            extracted = match.group("value").strip()
            break
    if not extracted:
        return None

    kind = "fact"
    if any(term in lower for term in ("prefer", "favorite", "favourite", "i like")):
        kind = "preference"
    elif "goal" in lower:
        kind = "goal"
    elif any(term in lower for term in ("always", "never", "when you", "please")):
        kind = "instruction"
    elif any(lower.startswith(prefix) for prefix in ("i am ", "i'm ", "my name ", "my company ")):
        kind = "profile"

    return {
        "kind": kind,
        "text": extracted[:1000],
        "confidence": 0.72,
    }
