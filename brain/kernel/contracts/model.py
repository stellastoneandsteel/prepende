"""ModelGateway — the swap point. VENDOR-NEUTRAL by design.

The boundary between the stable kernel and the volatile model world. The brain
is NOT tied to any one AI — Claude, GPT, Gemini, open-weight (Llama/Qwen/
DeepSeek), and fully local (Ollama/vLLM) are all just adapters, none
privileged. Everything model-specific lives behind an implementation of this
interface and nowhere else. Swapping to whatever model you want is a config
change that selects a different adapter; no caller changes.

Model choice can be per-call, not just one global model: the kernel may route
a cheap model for routine steps and a strong one for hard ones, and a council
tactic may deliberately use models from DIFFERENT vendors so their errors are
uncorrelated (heterogeneity makes the decisiveness layer stronger).

Generation and embeddings are selected independently (embeddings live behind
the same interface). Changing the embedding model changes the vector space, so
it triggers a re-index of the RAG projection — fine, since that index is a
rebuildable projection, never a master.

AUTH MODE is a first-class dimension, not just the vendor. An adapter
authenticates by one of:
  - api_key        — metered tokens (OpenAI, Google, Anthropic, OpenAI-compatible)
  - subscription   — a flat membership via the vendor's SANCTIONED OAuth path.
                     Today this is legitimately available only for Claude
                     (Anthropic's Pro/Max plan, as used by Claude Code). It is
                     often far better value than metered tokens for heavy use.
                     NOT available for ChatGPT Plus or Gemini Advanced — those
                     have no sanctioned programmatic sub access, and
                     reverse-engineering them violates ToS, so it is OFF-LIMITS
                     for a product we sell.
  - local          — Ollama / vLLM, $0, no account.
The gateway may prefer the cheapest legitimate path (e.g. a Claude membership
over Claude API) and fall back. New sanctioned subscription paths (if OpenAI/
Google open them) are added as adapters — a config change, never a rewrite.

Implementations: models/  (anthropic, openai, google, openai-compatible, local)
Rule: no model SDK is imported outside models/.

SKELETON — signatures only, no implementation yet.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, AsyncIterator, Sequence


class ModelGateway(ABC):
    """One interface to every model. Typed in, typed out."""

    @abstractmethod
    async def complete(self, messages: Sequence[dict[str, Any]], **opts: Any) -> Any:
        """Single completion. `messages` is provider-neutral; the adapter maps it."""

    @abstractmethod
    async def stream(
        self, messages: Sequence[dict[str, Any]], **opts: Any
    ) -> AsyncIterator[Any]:
        """Streaming completion."""

    @abstractmethod
    async def embed(self, texts: Sequence[str], **opts: Any) -> Sequence[Sequence[float]]:
        """Embeddings — feeds the MemoryStore and RAG index."""
