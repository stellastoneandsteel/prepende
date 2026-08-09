"""Server-side model provider calls for conversation routes."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Sequence

from kernel.core.config import Config
from models.factory import build_gateway
from prepende_brain.env import brand_env
from prepende_brain.private_fs import append_private_text


class ProviderService:
    def __init__(self, cfg: Config | None = None) -> None:
        self.cfg = cfg or Config()
        self.gateway = build_gateway(self.cfg)

    @property
    def provider_name(self) -> str:
        return str(getattr(self.gateway, "name", self.cfg.provider))

    @property
    def model_name(self) -> str:
        return str(getattr(self.gateway, "model", "") or self.provider_name)

    async def complete_conversation(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        user_id: str,
        conversation_id: str,
    ) -> str:
        started = time.time()
        try:
            result = await self.gateway.complete(messages, max_tokens=1200)
            self._log_call(user_id, conversation_id, messages, "ok", started)
            return str(result)
        except Exception:
            self._log_call(user_id, conversation_id, messages, "error", started)
            raise

    def _log_call(
        self,
        user_id: str,
        conversation_id: str,
        messages: Sequence[dict[str, Any]],
        status: str,
        started: float,
    ) -> None:
        path = Path(brand_env("PROVIDER_LOG", "./.engram/provider_requests.jsonl"))
        item = {
            "ts": time.time(),
            "durationMs": int((time.time() - started) * 1000),
            "status": status,
            "userId": user_id,
            "conversationId": conversation_id,
            "provider": self.provider_name,
            "model": self.model_name,
            "messageCount": len(messages),
            "inputChars": sum(len(str(msg.get("content", ""))) for msg in messages),
        }
        append_private_text(
            path,
            json.dumps(item, sort_keys=True) + "\n",
            repair_parent=path.parent.name == ".engram",
        )
