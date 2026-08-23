"""PromptRegistry — prompts as first-class, versioned assets.

Because models swap, prompts are an evolving asset, not string literals.
Source of truth is git-versioned files in prompts/. The registry resolves
which version is active, with a runtime override (per tenant) so a version
can be flipped without a redeploy. Same shape as the ModelGateway: a clean
swap point.

Impl: prompts/  (git files + a Postgres override row)
Optional: BAML (Apache-2.0) for typed structured-output prompts.
SKELETON — signatures only, no implementation yet.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class PromptRegistry(ABC):
    """Resolve (prompt_id -> active version) and render it."""

    @abstractmethod
    async def get(self, prompt_id: str, *, scope: str | None = None) -> str:
        """Return the active version of a prompt (scope may override the default)."""

    @abstractmethod
    async def set_active(self, prompt_id: str, version: str, *, scope: str | None = None) -> None:
        """Flip the active version. Per-scope override = no redeploy. Rollback = call again."""

    @abstractmethod
    async def render(self, prompt_id: str, variables: dict[str, object], *, scope: str | None = None) -> str:
        """Resolve + fill a prompt template."""
