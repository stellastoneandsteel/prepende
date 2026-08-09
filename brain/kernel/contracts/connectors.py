"""Connectors — the brain's hands and senses (outbound interop).

The brain is not only an MCP *server* others connect to (see interface/); it is
also a connector *hub* that reaches OUT to the world: external plugins and
connectors, and the owner's own products (e.g. your own tools and services). This is how
the Goal Loop acts on and senses the world — calling tools, pulling data,
driving other systems.

Standard-first: speak MCP as a CLIENT so any MCP-compliant tool/server is
reachable with no bespoke glue. Non-MCP systems get thin adapters behind this
same port. Discovery, auth, and per-connector scoping live here.

Impl: connectors/
SKELETON — signatures only, no implementation yet.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Sequence


class Connectors(ABC):
    """Discover and call external capabilities. The brain's outbound reach."""

    @abstractmethod
    async def register(self, spec: Any) -> str:
        """Register a connector (an MCP server endpoint, or an adapter for a non-MCP system)."""

    @abstractmethod
    async def list_tools(
        self, *, scope: str | None = None,
        tenant_id: str | None = None, workspace_id: str | None = None,
    ) -> Sequence[Any]:
        """Discover callable capabilities across all registered connectors."""

    @abstractmethod
    async def call(
        self, tool_id: str, args: dict[str, Any], *, scope: str | None = None,
        tenant_id: str | None = None, workspace_id: str | None = None,
    ) -> Any:
        """Invoke an external capability. Scope + guardrails enforced here, not assumed."""
