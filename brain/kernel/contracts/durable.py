"""Versioned contracts for durable, resumable goal execution.

The structured checkpoint is authoritative. Provider-native compacted context
is an optional acceleration and may be discarded without losing the ability to
resume. Deliberative chain-of-thought is deliberately absent: Prepende stores
decisions, constraints, evidence, receipts, and next actions.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any, Awaitable, Callable, Mapping


DURABLE_SCHEMA_VERSION = "prepende-durable-goal-v2"
CONTEXT_SCHEMA_VERSION = "prepende-context-snapshot-v1"
CHECKPOINT_SCHEMA_VERSION = "prepende-checkpoint-v1"


@dataclass(frozen=True)
class TaskNode:
    id: str
    title: str
    dependencies: tuple[str, ...] = ()
    acceptance: tuple[str, ...] = ()
    idempotency_key: str = ""
    tool_policy: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "dependencies": list(self.dependencies),
            "acceptance": list(self.acceptance),
            "idempotencyKey": self.idempotency_key,
            "toolPolicy": dict(self.tool_policy),
        }


@dataclass(frozen=True)
class ContextSnapshot:
    goal: str
    plan_version: int
    decisions: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    completed_tasks: tuple[str, ...] = ()
    artifact_hashes: Mapping[str, str] = field(default_factory=dict)
    evidence_refs: tuple[str, ...] = ()
    tool_receipts: tuple[Mapping[str, Any], ...] = ()
    approvals: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()
    next_task_id: str | None = None
    budget: Mapping[str, Any] = field(default_factory=dict)
    model_provenance: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = CONTEXT_SCHEMA_VERSION

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        return {
            "schemaVersion": value.pop("schema_version"),
            "goal": value["goal"],
            "planVersion": value["plan_version"],
            "decisions": list(value["decisions"]),
            "constraints": list(value["constraints"]),
            "completedTasks": list(value["completed_tasks"]),
            "artifactHashes": dict(value["artifact_hashes"]),
            "evidenceRefs": list(value["evidence_refs"]),
            "toolReceipts": list(value["tool_receipts"]),
            "approvals": list(value["approvals"]),
            "blockers": list(value["blockers"]),
            "nextTaskId": value["next_task_id"],
            "budget": dict(value["budget"]),
            "modelProvenance": dict(value["model_provenance"]),
        }


@dataclass(frozen=True)
class CompactionEnvelope:
    kind: str
    digest: str
    summary: str
    provider: str | None = None
    opaque_ref: str | None = None
    schema_version: str = "prepende-compaction-envelope-v1"

    def as_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "kind": self.kind,
            "digest": self.digest,
            "summary": self.summary,
            "provider": self.provider,
            "opaqueRef": self.opaque_ref,
        }


@dataclass(frozen=True)
class Checkpoint:
    id: str
    run_id: str
    sequence: int
    task_id: str | None
    phase: str
    context: ContextSnapshot
    verification: Mapping[str, Any]
    checksum: str
    compaction: CompactionEnvelope | None = None
    schema_version: str = CHECKPOINT_SCHEMA_VERSION


@dataclass(frozen=True)
class GoalRun:
    id: str
    scope: str
    workspace_id: str
    goal: str
    status: str
    plan_version: int
    active_task_id: str | None
    lease_owner: str | None
    lease_expires_at: float | None
    budget: Mapping[str, Any] = field(default_factory=dict)
    model_route: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = DURABLE_SCHEMA_VERSION


class ContextCompactor(ABC):
    """Compress a structured snapshot without becoming its source of truth."""

    @abstractmethod
    async def compact(self, snapshot: ContextSnapshot) -> CompactionEnvelope:
        """Return a bounded, integrity-addressed continuation envelope."""


class DurableExecution(ABC):
    """Run a goal reliably across crashes, leases, and context windows."""

    @abstractmethod
    async def submit(self, fn: Callable[..., Awaitable[Any]], *args: Any, **kwargs: Any) -> str:
        """Enqueue durable work. Returns a run id."""

    @abstractmethod
    async def status(self, run_id: str) -> Any:
        """Inspect a run."""

    @abstractmethod
    async def cancel(self, run_id: str) -> None:
        """Stop a run (and respect the hard cost/retry ceilings that bound it)."""

    @abstractmethod
    async def resume(self, run_id: str) -> Any:
        """Resume from the latest verified checkpoint without replaying done tasks."""
