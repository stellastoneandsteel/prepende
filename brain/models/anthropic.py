"""AnthropicGateway — Claude via the Messages API. Stdlib HTTP, no SDK dependency.

API key mode for Phase 0. (Subscription/OAuth mode — using a Claude Pro/Max
membership, the way Claude Code does — is the sanctioned higher-value path noted
in kernel/contracts/model.py; it lands as its own adapter later.)
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import urllib.error
from typing import Any, AsyncIterator, Sequence

from kernel.contracts import ModelGateway
from models.catalog import ANTHROPIC_FABLE, resolve_model_id
from models._http import http_error_text, post_json

_ENDPOINT = "https://api.anthropic.com/v1/messages"
_UNSET = object()


class AnthropicGateway(ModelGateway):
    name = "anthropic"

    def __init__(self, api_key: str, model: str = "", fallback_models: Sequence[str] = ()) -> None:
        self.api_key = api_key
        self.model = resolve_model_id("anthropic", model) or ANTHROPIC_FABLE
        self.requested_model = self.model
        self._last_resolved_model: str | None = None
        self._resolved_context: contextvars.ContextVar[object] = contextvars.ContextVar(
            f"prepende_anthropic_resolved_{id(self)}",
            default=_UNSET,
        )
        self.fallback_models = tuple(fallback_models)

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
        """Fall back only when Anthropic rejected the selected *model*.

        Anthropic also returns HTTP 400 for account billing failures. Treating
        every 400 as model unavailability made a depleted account walk the
        whole model chain and hid the real blocker. Authentication, billing,
        quota, permission, and generic rate-limit failures are terminal.
        """

        if not isinstance(exc, urllib.error.HTTPError):
            return False
        body = http_error_text(exc).lower()
        terminal_markers = (
            "credit balance",
            "billing",
            "payment",
            "api key",
            "authentication",
            "permission",
            "organization",
            "rate limit",
            "quota",
        )
        if any(marker in body for marker in terminal_markers):
            return False
        model_markers = (
            "model",
            "not found",
            "not available",
            "unavailable",
            "unsupported",
        )
        return exc.code in {400, 404, 409, 422} and any(marker in body for marker in model_markers)

    def _call_once(self, messages: Sequence[dict[str, Any]], max_tokens: int, system: str = "", model: str = "") -> str:
        # Anthropic takes the system prompt as a top-level field (not a chat role);
        # also lift any role:"system" messages out for safety.
        sys_parts = [system] if system else []
        chat = []
        for m in messages:
            if m.get("role") == "system":
                sys_parts.append(m["content"])
            else:
                chat.append({"role": m.get("role", "user"), "content": m["content"]})
        payload: dict[str, Any] = {"model": model or self.requested_model, "max_tokens": max_tokens, "messages": chat}
        if sys_parts:
            payload["system"] = "\n\n".join(sys_parts)
        body = json.dumps(payload).encode()
        headers = {"x-api-key": self.api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
        data = json.loads(post_json(_ENDPOINT, body, headers))
        return "".join(b.get("text", "") for b in data.get("content", []))

    def _call_with_resolution(
        self,
        messages: Sequence[dict[str, Any]],
        max_tokens: int,
        system: str = "",
    ) -> tuple[str, str | None]:
        candidates = tuple(dict.fromkeys((self.requested_model, *self.fallback_models)))
        last_error: Exception | None = None
        for index, candidate in enumerate(candidates):
            try:
                answer = self._call_once(messages, max_tokens, system, candidate)
                resolved = candidate if candidate != self.requested_model else None
                return answer, resolved
            except Exception as exc:
                last_error = exc
                if not self._should_fallback(exc) or index == len(candidates) - 1:
                    raise
        raise last_error or RuntimeError("Anthropic model fallback failed")

    def _call(self, messages: Sequence[dict[str, Any]], max_tokens: int, system: str = "") -> str:
        answer, resolved = self._call_with_resolution(messages, max_tokens, system)
        self._record_resolution(resolved)
        return answer

    async def complete(self, messages: Sequence[dict[str, Any]], **opts: Any) -> str:
        answer, resolved = await asyncio.to_thread(
            self._call_with_resolution,
            messages,
            int(opts.get("max_tokens", 1024)),
            opts.get("system", ""),
        )
        self._record_resolution(resolved)
        return answer

    async def stream(self, messages: Sequence[dict[str, Any]], **opts: Any) -> AsyncIterator[str]:
        # Phase 0: fetch once, then emit in word chunks for a streaming feel.
        # Real token streaming (SSE) is a refinement behind this same interface.
        text = await self.complete(messages, **opts)
        for word in text.split(" "):
            yield word + " "

    async def embed(self, texts: Sequence[str], **opts: Any) -> Sequence[Sequence[float]]:
        raise NotImplementedError("embeddings arrive in Phase 1 (memory)")
