"""GoogleGateway — Gemini via the Generative Language API. Stdlib HTTP, no SDK.

API-key mode (Gemini Advanced consumer subs have no sanctioned programmatic
path; the key is the way, per kernel/contracts/model.py).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator, Sequence

from kernel.contracts import ModelGateway
from models._http import post_json


class GoogleGateway(ModelGateway):
    name = "google"

    def __init__(self, api_key: str, model: str = "") -> None:
        self.api_key = api_key
        self.model = model or "gemini-2.0-flash"

    def _call(self, messages: Sequence[dict[str, Any]], max_tokens: int, system: str = "") -> str:
        sys_parts = [system] if system else []
        contents = []
        for m in messages:
            if m.get("role") == "system":
                sys_parts.append(m["content"])
            else:
                contents.append({"role": ("model" if m.get("role") == "assistant" else "user"),
                                 "parts": [{"text": m["content"]}]})
        payload: dict[str, Any] = {"contents": contents, "generationConfig": {"maxOutputTokens": max_tokens}}
        if sys_parts:  # Gemini takes a system prompt as systemInstruction
            payload["systemInstruction"] = {"parts": [{"text": "\n\n".join(sys_parts)}]}
        body = json.dumps(payload).encode()
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}"
            f":generateContent?key={self.api_key}"
        )
        data = json.loads(post_json(url, body, {"content-type": "application/json"}))
        cands = data.get("candidates", [])
        if not cands:
            return ""
        parts = cands[0].get("content", {}).get("parts", [])
        return "".join(p.get("text", "") for p in parts)

    async def complete(self, messages: Sequence[dict[str, Any]], **opts: Any) -> str:
        return await asyncio.to_thread(self._call, messages, int(opts.get("max_tokens", 1024)), opts.get("system", ""))

    async def stream(self, messages: Sequence[dict[str, Any]], **opts: Any) -> AsyncIterator[str]:
        text = await self.complete(messages, **opts)
        for word in text.split(" "):
            yield word + " "

    async def embed(self, texts: Sequence[str], **opts: Any) -> Sequence[Sequence[float]]:
        raise NotImplementedError("embeddings arrive in Phase 1 (memory)")
