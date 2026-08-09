"""EchoGateway — zero-config, zero-cost provider so Prepende runs out of the box.

No key, no network. Proves the Goal Loop, Workspace, and TUI end to end before
you wire a real model. Swap to a real provider by setting MODEL_PROVIDER in .env.
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Sequence

from kernel.contracts import ModelGateway


class EchoGateway(ModelGateway):
    name = "echo"

    async def complete(self, messages: Sequence[dict[str, Any]], **opts: Any) -> str:
        last = messages[-1]["content"] if messages else ""
        return (
            f"[echo provider] You set the goal: \"{last}\". "
            "I would now research, plan, and pursue it. "
            "Wire a real model in .env (MODEL_PROVIDER=anthropic|openai|...) to get a real answer."
        )

    async def stream(self, messages: Sequence[dict[str, Any]], **opts: Any) -> AsyncIterator[str]:
        text = await self.complete(messages, **opts)
        for word in text.split(" "):
            yield word + " "

    async def embed(self, texts: Sequence[str], **opts: Any) -> Sequence[Sequence[float]]:
        raise NotImplementedError("embeddings arrive in Phase 1 (memory)")
