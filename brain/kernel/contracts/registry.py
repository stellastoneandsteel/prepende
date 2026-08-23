"""Registry — the catalog of everything the brain can orchestrate.

One honest catalog of every orchestratable unit: tactics, n8n workflows,
connector tools, and agents. This is descriptive METADATA + SELECTION only —
the registry never executes anything and performs no external action. The
Strategist/router and receipts/UI read from it as the single source of truth
for "what's available, is it ready, does it need approval."

This is a port (contract). The in-memory implementation and the seed from the
existing sources live in kernel/core/registry.py. See
docs/ENGRAM_ORCHESTRATION_REGISTRY_PLAN.md.

SKELETON+: the metadata is real and listable, but the Strategist does NOT
consume the registry yet — Goal Loop routing behavior is unchanged. Wiring the
router onto the registry is a later, separate lane.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

# Shared status vocabulary — aligned across the registry, receipts, the UI, and
# the builder Workbench. Do NOT invent softer labels.
READINESS_VALUES: tuple[str, ...] = (
    "live",                 # working now and verified against a real endpoint/file/deploy
    "read_only",            # callable for reading only; no write/send/spend possible
    "staged",               # designed/represented, not executing real behavior yet
    "dry_run",              # can produce a draft/receipt with no external side effect
    "approval_required",    # needs explicit human approval before it can proceed
    "blocked",              # cannot proceed until a named missing dependency is resolved
    "mocked",               # placeholder/synthetic state; must be labeled
    "membership_required",  # hidden until account/auth/billing/entitlement checks pass
    "needs_verification",   # may exist, but no current receipt proves it
)

# Kinds of orchestratable unit the registry catalogs.
KINDS: tuple[str, ...] = ("tactic", "workflow", "connector_tool", "agent")


@dataclass(frozen=True)
class RegistryEntry:
    """One orchestratable unit, as honest catalog metadata (never behavior).

    `external_actions` defaults to "none": the registry entry itself executes
    nothing. `approval_required` gates whether *invoking* the unit (in a later
    execution lane) needs human approval. `readiness` is one of
    READINESS_VALUES.
    """

    id: str                          # namespaced: tactic.solo, workflow.<name>, connector.<id>, agent.scout
    kind: str                        # one of KINDS
    name: str
    when_to_use: str = ""            # the selection signal (what it's good for)
    readiness: str = "staged"        # one of READINESS_VALUES
    external_actions: str = "none"   # "none" | description of the side effect
    approval_required: bool = False
    estimate: dict[str, Any] = field(default_factory=dict)  # e.g. {"calls": 1, "risk": "low"}
    scopes: tuple[str, ...] = ()     # least-privilege scopes touched (read-only vs write)
    source: str = ""                 # where it was registered from
    reason: str = ""                 # why blocked/needs_key/etc., when relevant

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"unknown registry kind: {self.kind!r} (expected one of {KINDS})")
        if self.readiness not in READINESS_VALUES:
            raise ValueError(f"unknown readiness: {self.readiness!r} (expected one of {READINESS_VALUES})")


class Registry(ABC):
    """Catalog of orchestratable units. Metadata + query only — never execution."""

    @abstractmethod
    def register(self, entry: RegistryEntry) -> str:
        """Add or replace an entry; returns its id."""

    @abstractmethod
    def get(self, entry_id: str) -> Optional[RegistryEntry]:
        """Return the entry with this id, or None."""

    @abstractmethod
    def list(self) -> list[RegistryEntry]:
        """All entries."""

    @abstractmethod
    def query(self, *, kind: str | None = None, readiness: str | None = None) -> list[RegistryEntry]:
        """Entries filtered by kind and/or readiness."""
