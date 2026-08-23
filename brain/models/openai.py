"""OpenAIGateway — OpenAI Chat Completions shape. Stdlib HTTP, no SDK dependency.

One adapter covers three of our auth targets, because they all speak the
OpenAI API:
  - openai             (api.openai.com)
  - openai-compatible  (OpenRouter / Together / Groq / Fireworks / vLLM)
  - local              (Ollama / vLLM on your own machine)
The only differences are base_url and key, so the gateway stays one small class.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import os
import urllib.error
from typing import Any, AsyncIterator, Sequence

from prepende_brain.env import brand_env

from kernel.contracts import ModelGateway
from models.catalog import OPENAI_SOL, OPENAI_TERRA, OPENAI_LUNA, resolve_model_id
from models._http import post_json


_UNSET = object()


class OpenAIGateway(ModelGateway):
    def __init__(
        self,
        api_key: str,
        model: str = "",
        base_url: str = "https://api.openai.com/v1",
        name: str = "openai",
        fallback_models: Sequence[str] = (),
    ) -> None:
        self.api_key = api_key
        self.model = resolve_model_id("openai", model) if name == "openai" else (model or OPENAI_SOL)
        self.requested_model = self.model
        self._last_resolved_model: str | None = None
        self._resolved_context: contextvars.ContextVar[object] = contextvars.ContextVar(
            f"prepende_openai_resolved_{id(self)}",
            default=_UNSET,
        )
        self.fallback_models = tuple(fallback_models)
        self.base = base_url.rstrip("/")
        self.name = name

    @property
    def resolved_model(self) -> str | None:
        value = self._resolved_context.get()
        if value is _UNSET:
            return self._last_resolved_model
        return value if isinstance(value, str) else None

    def _record_resolution(self, value: str | None) -> None:
        self._last_resolved_model = value
        self._resolved_context.set(value)

    @staticmethod
    def _should_fallback(exc: Exception) -> bool:
        # A bad key/permission must fail loudly; only model availability and
        # quota responses should move down the configured model chain.
        return isinstance(exc, urllib.error.HTTPError) and exc.code in {400, 404, 409, 422, 429}

    def _call_once(
        self,
        messages: Sequence[dict[str, Any]],
        max_tokens: int,
        system: str = "",
        reasoning_effort: str = "",
        model: str = "",
    ) -> str:
        msgs = [{"role": m.get("role", "user"), "content": m["content"]} for m in messages]
        if system:  # OpenAI takes system as a leading message
            msgs = [{"role": "system", "content": system}] + msgs
        body_payload: dict[str, Any] = {
            "model": model or self.requested_model,
            "messages": msgs,
        }
        # GPT-5.6 uses the current completion-token field. Keep the legacy
        # field for non-5.6 OpenAI-compatible endpoints that may not support it.
        selected_model = model or self.requested_model
        token_field = (
            "max_completion_tokens"
            if self.name == "openai" and selected_model in {OPENAI_SOL, OPENAI_TERRA, OPENAI_LUNA}
            else "max_tokens"
        )
        body_payload[token_field] = max_tokens
        effort = (reasoning_effort or os.environ.get("OPENAI_REASONING_EFFORT", "")).strip().lower()
        if effort and self.name == "openai":
            effort = {"med": "medium", "ex-high": "xhigh"}.get(effort, effort)
            if effort not in {"none", "minimal", "low", "medium", "high", "xhigh"}:
                raise ValueError("OPENAI_REASONING_EFFORT must be none, minimal, low, medium, high, or xhigh")
            body_payload["reasoning_effort"] = effort
        body = json.dumps(body_payload).encode()
        headers = {"content-type": "application/json"}
        if self.api_key:
            headers["authorization"] = f"Bearer {self.api_key}"
        data = json.loads(post_json(self.base + "/chat/completions", body, headers))
        return data["choices"][0]["message"]["content"]

    def _call_with_resolution(
        self,
        messages: Sequence[dict[str, Any]],
        max_tokens: int,
        system: str = "",
        reasoning_effort: str = "",
    ) -> tuple[str, str | None]:
        candidates = tuple(dict.fromkeys((self.requested_model, *self.fallback_models)))
        last_error: Exception | None = None
        for index, candidate in enumerate(candidates):
            try:
                answer = self._call_once(messages, max_tokens, system, reasoning_effort, candidate)
                resolved = candidate if candidate != self.requested_model else None
                return answer, resolved
            except Exception as exc:
                last_error = exc
                if not self._should_fallback(exc) or index == len(candidates) - 1:
                    raise
        raise last_error or RuntimeError("OpenAI model fallback failed")

    def _call(
        self,
        messages: Sequence[dict[str, Any]],
        max_tokens: int,
        system: str = "",
        reasoning_effort: str = "",
    ) -> str:
        answer, resolved = self._call_with_resolution(messages, max_tokens, system, reasoning_effort)
        self._record_resolution(resolved)
        return answer

    async def complete(self, messages: Sequence[dict[str, Any]], **opts: Any) -> str:
        answer, resolved = await asyncio.to_thread(
            self._call_with_resolution,
            messages,
            int(opts.get("max_tokens", 1024)),
            opts.get("system", ""),
            opts.get("reasoning_effort", ""),
        )
        self._record_resolution(resolved)
        return answer

    async def stream(self, messages: Sequence[dict[str, Any]], **opts: Any) -> AsyncIterator[str]:
        text = await self.complete(messages, **opts)
        for word in text.split(" "):
            yield word + " "

    async def embed(self, texts: Sequence[str], **opts: Any) -> Sequence[Sequence[float]]:
        model = str(opts.get("model") or brand_env("EMBEDDING_MODEL") or "text-embedding-3-small")
        body = json.dumps({"model": model, "input": list(texts)}).encode()
        headers = {"content-type": "application/json"}
        if self.api_key:
            headers["authorization"] = f"Bearer {self.api_key}"
        data = json.loads(await asyncio.to_thread(post_json, self.base + "/embeddings", body, headers))
        return [item["embedding"] for item in data["data"]]
