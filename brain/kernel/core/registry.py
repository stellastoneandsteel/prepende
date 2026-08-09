"""InMemoryRegistry + seeding — the orchestration catalog implementation.

Catalogs the orchestratable units from the EXISTING sources (tactics,
WorkflowSelector, ConnectorHub, agents) without changing how any of them run.
Pure cataloging: no execution, no external action. The Strategist does not read
this yet — Goal Loop behavior is unchanged.

See kernel/contracts/registry.py for the port and
docs/ENGRAM_ORCHESTRATION_REGISTRY_PLAN.md for the design.
"""

from __future__ import annotations

from typing import Any, Optional

from kernel.contracts.registry import Registry, RegistryEntry
from kernel.core.types import Goal


class InMemoryRegistry(Registry):
    """A thin, stdlib-only in-memory catalog. Insertion-ordered."""

    def __init__(self) -> None:
        self._entries: dict[str, RegistryEntry] = {}

    def register(self, entry: RegistryEntry) -> str:
        self._entries[entry.id] = entry
        return entry.id

    def get(self, entry_id: str) -> Optional[RegistryEntry]:
        return self._entries.get(entry_id)

    def list(self) -> list[RegistryEntry]:
        return list(self._entries.values())

    def query(self, *, kind: str | None = None, readiness: str | None = None) -> list[RegistryEntry]:
        out = self.list()
        if kind is not None:
            out = [e for e in out if e.kind == kind]
        if readiness is not None:
            out = [e for e in out if e.readiness == readiness]
        return out


# --- Seeding from existing sources (descriptive only; nothing executes) -------

_TACTIC_WHEN = {
    "solo": "Default. One agent loop; fast, carries chat history. Most goals land here.",
    "hierarchical": "Multi-step projects — decompose into manager/worker steps.",
    "parallel_explore": "Generative goals — explore N attempts, pick the best.",
    "council_debate": "High-stakes judgment calls — debate, then decide.",
}


def seed_tactics(registry: Registry, gateway: Any) -> None:
    """Catalog the built-in tactics. Tactics are local reasoning — always
    available, no external action."""
    from tactics.solo import SoloTactic
    from tactics.hierarchical import HierarchicalTactic
    from tactics.parallel_explore import ParallelExploreTactic
    from tactics.council import CouncilDebateTactic

    probe = Goal(text="")
    for cls in (SoloTactic, HierarchicalTactic, ParallelExploreTactic, CouncilDebateTactic):
        tactic = cls(gateway)
        try:
            estimate = dict(tactic.estimate(probe, {}))
        except Exception:
            estimate = {}
        registry.register(RegistryEntry(
            id=f"tactic.{tactic.name}",
            kind="tactic",
            name=tactic.name,
            when_to_use=_TACTIC_WHEN.get(tactic.name, ""),
            readiness="live",            # local reasoning is always available
            external_actions="none",
            approval_required=False,
            estimate=estimate,
            scopes=("local",),
            source="tactics",
        ))


def seed_workflows(registry: Registry, workflows: Any) -> None:
    """Catalog configured n8n workflows. They are approval-gated dry-run: nothing
    executes until a human approves (see /v1/workflows)."""
    if workflows is None:
        return
    try:
        menu = workflows.list()
    except Exception:
        menu = []
    for entry in menu or []:
        name = entry.get("name") if isinstance(entry, dict) else None
        if not name:
            continue
        registry.register(RegistryEntry(
            id=f"workflow.{name}",
            kind="workflow",
            name=name,
            when_to_use=(entry.get("description", "") if isinstance(entry, dict) else ""),
            readiness="approval_required",
            external_actions="none",      # nothing runs until approved
            approval_required=True,
            scopes=("n8n",),
            source="workflows.json",
        ))


# Known agents: gather -> verify -> pending_review -> human accept. Nothing
# durable or external happens without the human-accept gate, so they are
# dry-run + approval_required.
_KNOWN_AGENTS = (
    ("scout", "Orchestrates research+verify into a reviewable knowledge item.", "agents.scout", "KnowledgeScout"),
    ("research", "Gathers candidate facts/sources for a topic (reviewable).", "agents.research", "ResearchAgent"),
    ("verify", "Checks gathered sources before anything is accepted.", "agents.verify", "SourceVerifyAgent"),
)


def seed_agents(registry: Registry) -> None:
    """Catalog the known knowledge agents if their modules import cleanly."""
    import importlib

    for agent_id, when, module_path, cls_name in _KNOWN_AGENTS:
        try:
            module = importlib.import_module(module_path)
            if not hasattr(module, cls_name):
                continue
        except Exception:
            continue
        registry.register(RegistryEntry(
            id=f"agent.{agent_id}",
            kind="agent",
            name=agent_id,
            when_to_use=when,
            readiness="dry_run",          # produces a pending_review item; nothing durable
            external_actions="none",
            approval_required=True,        # human accept before it reaches memory/vault
            scopes=("read",),
            source="agents",
        ))


def seed_registry(registry: Registry, *, gateway: Any, workflows: Any = None, agents: bool = True) -> Registry:
    """Seed the synchronous sources (tactics, workflows, agents). Connector tools
    are async — seed them with `seed_connector_tools` from an async surface."""
    seed_tactics(registry, gateway)
    seed_workflows(registry, workflows)
    if agents:
        seed_agents(registry)
    return registry


async def seed_connector_tools(registry: Registry, connectors: Any) -> None:
    """Catalog connector tools from the hub. A tool with its key set is callable
    (`needs_verification` — no receipt proves its scope yet); without its key it
    is `blocked`. Calling any connector tool is gated (approval_required); the
    registry itself never calls anything."""
    if connectors is None:
        return
    try:
        tools = await connectors.list_tools()
    except Exception:
        tools = []
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        tool_id = tool.get("id")
        if not tool_id:
            continue
        configured = bool(tool.get("configured", tool.get("ready")))
        state = str(tool.get("readiness") or ("configured" if configured else "unknown"))
        registry.register(RegistryEntry(
            id=f"connector.{tool_id}",
            kind="connector_tool",
            name=str(tool_id),
            when_to_use=f"{tool.get('kind', '')} tool via the {tool.get('connector', '')} connector".strip(),
            readiness="needs_verification" if configured else "blocked",
            external_actions="none",       # the catalog executes nothing; the call is a separate gated step
            approval_required=True,         # invoking a connector tool is gated
            scopes=(str(tool.get("kind", "")),),
            source="connectors.hub",
            reason=(
                f"connector readiness is {state}; fresh same-scope probe required"
                if configured else
                "connector is not configured (set its auth_env in .env — Prepende's own key)"
            ),
        ))
