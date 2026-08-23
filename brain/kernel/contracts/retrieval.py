"""Typed contracts for bounded, dependency-aware retrieval graphs.

Retrieval nodes are read-only computations.  They carry exact tenant/workspace
identity, explicit data dependencies, bounded query/result budgets, and
server-owned run conditions.  They never grant connectors, approvals, durable
memory writes, or external actions.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
import hashlib
import json
import math
import re
from typing import Any, Literal


RetrievalRunCondition = Literal[
    "always",
    "dependencies_succeeded",
    "any_dependency_empty",
    "all_dependencies_empty",
]
RetrievalNodeStatus = Literal["succeeded", "failed", "skipped"]
RetrievalStopReason = Literal[
    "pass",
    "partial",
    "no_evidence",
    "required_node_failed",
]

_RUN_CONDITIONS = frozenset({
    "always",
    "dependencies_succeeded",
    "any_dependency_empty",
    "all_dependencies_empty",
})
_NODE_STATUSES = frozenset({"succeeded", "failed", "skipped"})
_STOP_REASONS = frozenset({
    "pass", "partial", "no_evidence", "required_node_failed",
})
_STABLE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")


def _canonical_value(value: Any) -> Any:
    """Return the self-contained JSON representation used by retrieval hashes."""
    if is_dataclass(value) and not isinstance(value, type):
        return {
            item.name: _canonical_value(getattr(value, item.name))
            for item in fields(value)
        }
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("canonical mappings require string keys")
        return {key: _canonical_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_canonical_value(item) for item in value]
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("canonical values cannot contain non-finite floats")
        return value
    raise TypeError(f"unsupported canonical value: {type(value).__name__}")


def canonical_hash(value: Any) -> str:
    """Hash a retrieval value without depending on the private orchestrator."""
    payload = json.dumps(
        _canonical_value(value),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _stable_id(label: str, value: str) -> str:
    if not isinstance(value, str) or not _STABLE_ID.fullmatch(value):
        raise ValueError(f"{label} must be a stable identifier")
    return value


def _bounded_text(label: str, value: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} is required")
    if len(value) > maximum:
        raise ValueError(f"{label} must be at most {maximum} characters")
    return value.strip()


@dataclass(frozen=True)
class RetrievalQueryNode:
    """One bounded query plus the exact edge conditions that admit it."""

    node_id: str
    query: str
    depends_on: tuple[str, ...] = ()
    run_if: RetrievalRunCondition = "always"
    required: bool = False
    k: int = 5
    timeout_ms: int = 10_000

    def __post_init__(self) -> None:
        object.__setattr__(self, "node_id", _stable_id("node_id", self.node_id))
        object.__setattr__(self, "query", _bounded_text("query", self.query, 2_000))
        if len(self.depends_on) != len(set(self.depends_on)):
            raise ValueError("depends_on entries must be unique")
        for dependency in self.depends_on:
            _stable_id("dependency", dependency)
        if self.node_id in self.depends_on:
            raise ValueError("a retrieval node cannot depend on itself")
        if self.run_if not in _RUN_CONDITIONS:
            raise ValueError("invalid retrieval run condition")
        if not self.depends_on and self.run_if != "always":
            raise ValueError("a root retrieval node must use run_if=always")
        if not isinstance(self.required, bool):
            raise TypeError("required must be boolean")
        if isinstance(self.k, bool) or not isinstance(self.k, int) or not 1 <= self.k <= 25:
            raise ValueError("k must be an integer between one and 25")
        if (
            isinstance(self.timeout_ms, bool)
            or not isinstance(self.timeout_ms, int)
            or not 1 <= self.timeout_ms <= 60_000
        ):
            raise ValueError("timeout_ms must be between one and 60000")

    @property
    def query_hash(self) -> str:
        return canonical_hash({"query": self.query})


@dataclass(frozen=True)
class RetrievalPlan:
    """A small acyclic retrieval graph with a hard convergence ceiling."""

    plan_id: str
    tenant_id: str
    workspace_id: str
    nodes: tuple[RetrievalQueryNode, ...]
    max_parallelism: int = 4
    max_rounds: int = 2
    max_hits: int = 16

    def __post_init__(self) -> None:
        for label in ("plan_id", "tenant_id", "workspace_id"):
            object.__setattr__(self, label, _stable_id(label, getattr(self, label)))
        if not isinstance(self.nodes, tuple) or not self.nodes:
            raise ValueError("a retrieval plan requires at least one node")
        if len(self.nodes) > 8:
            raise ValueError("a retrieval plan is limited to eight nodes")
        node_ids = [item.node_id for item in self.nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("retrieval node ids must be unique")
        known = set(node_ids)
        for node in self.nodes:
            missing = set(node.depends_on) - known
            if missing:
                raise ValueError(f"retrieval dependencies are missing: {sorted(missing)}")
        if (
            isinstance(self.max_parallelism, bool)
            or not isinstance(self.max_parallelism, int)
            or not 1 <= self.max_parallelism <= 8
        ):
            raise ValueError("max_parallelism must be between one and eight")
        if (
            isinstance(self.max_rounds, bool)
            or not isinstance(self.max_rounds, int)
            or not 1 <= self.max_rounds <= 2
        ):
            raise ValueError("max_rounds must be one or two")
        if (
            isinstance(self.max_hits, bool)
            or not isinstance(self.max_hits, int)
            or not 1 <= self.max_hits <= 64
        ):
            raise ValueError("max_hits must be between one and 64")

        by_id = {item.node_id: item for item in self.nodes}
        visiting: set[str] = set()
        depths: dict[str, int] = {}

        def depth(node_id: str) -> int:
            if node_id in depths:
                return depths[node_id]
            if node_id in visiting:
                raise ValueError("retrieval dependencies must form an acyclic graph")
            visiting.add(node_id)
            dependencies = by_id[node_id].depends_on
            value = 1 if not dependencies else 1 + max(depth(item) for item in dependencies)
            visiting.remove(node_id)
            depths[node_id] = value
            return value

        if max(depth(node_id) for node_id in node_ids) > self.max_rounds:
            raise ValueError("retrieval graph depth exceeds max_rounds")

    @property
    def plan_hash(self) -> str:
        return canonical_hash(self)


@dataclass(frozen=True)
class RetrievalNodeReceipt:
    """Runtime-validated output contract for one query node."""

    node_id: str
    query_hash: str
    status: RetrievalNodeStatus
    depends_on: tuple[str, ...]
    run_if: RetrievalRunCondition
    required: bool
    retrieved: int
    accepted: int
    rejected: int
    output_hash: str
    error: str = ""

    def __post_init__(self) -> None:
        _stable_id("node_id", self.node_id)
        if len(self.depends_on) != len(set(self.depends_on)):
            raise ValueError("receipt dependencies must be unique")
        for dependency in self.depends_on:
            _stable_id("receipt dependency", dependency)
        if self.status not in _NODE_STATUSES:
            raise ValueError("invalid retrieval node status")
        if self.run_if not in _RUN_CONDITIONS:
            raise ValueError("invalid retrieval run condition")
        if not isinstance(self.required, bool):
            raise TypeError("receipt required must be boolean")
        for value in (self.retrieved, self.accepted, self.rejected):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError("retrieval counts must be non-negative integers")
        if self.accepted + self.rejected != self.retrieved:
            raise ValueError("accepted plus rejected must equal retrieved")
        for label, value in (("query_hash", self.query_hash), ("output_hash", self.output_hash)):
            if not isinstance(value, str) or not _SHA256.fullmatch(value):
                raise ValueError(f"{label} must be a sha256 content hash")
        if self.status == "failed" and not self.error:
            raise ValueError("a failed retrieval node requires an error")
        if self.status != "failed" and self.error:
            raise ValueError("only failed retrieval nodes may carry an error")


@dataclass(frozen=True)
class RetrievalGraphReceipt:
    """Terminal graph receipt derived from actual runtime batches."""

    plan_id: str
    plan_hash: str
    tenant_id: str
    workspace_id: str
    stop_reason: RetrievalStopReason
    nodes: tuple[RetrievalNodeReceipt, ...]
    parallel_groups: tuple[tuple[str, ...], ...]
    max_parallelism: int
    max_observed_parallelism: int
    rounds_used: int
    returned_hits: int
    duplicates_dropped: int

    def __post_init__(self) -> None:
        for label in ("plan_id", "tenant_id", "workspace_id"):
            _stable_id(label, getattr(self, label))
        if self.stop_reason not in _STOP_REASONS:
            raise ValueError("invalid retrieval stop reason")
        if not isinstance(self.nodes, tuple) or not self.nodes:
            raise ValueError("a retrieval graph receipt requires node receipts")
        node_ids = [item.node_id for item in self.nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("retrieval graph receipt node ids must be unique")
        if not 1 <= self.max_observed_parallelism <= self.max_parallelism <= 8:
            raise ValueError("invalid observed retrieval parallelism")
        if not 1 <= self.rounds_used <= 2:
            raise ValueError("invalid retrieval rounds_used")
        if self.returned_hits < 0 or self.duplicates_dropped < 0:
            raise ValueError("retrieval graph counts must be non-negative")
        for label, value in (("plan_hash", self.plan_hash),):
            if not isinstance(value, str) or not _SHA256.fullmatch(value):
                raise ValueError(f"{label} must be a sha256 content hash")
        known = set(node_ids)
        for node in self.nodes:
            if set(node.depends_on) - known:
                raise ValueError("receipt dependencies must name receipt nodes")
        if not isinstance(self.parallel_groups, tuple):
            raise TypeError("parallel_groups must be a tuple")
        executed: list[str] = []
        for group in self.parallel_groups:
            if not isinstance(group, tuple) or not group:
                raise ValueError("parallel groups must be non-empty tuples")
            if len(group) > self.max_parallelism:
                raise ValueError("parallel group exceeds max_parallelism")
            if len(group) != len(set(group)) or set(group) - known:
                raise ValueError("parallel group node ids are invalid")
            executed.extend(group)
        if len(executed) != len(set(executed)):
            raise ValueError("a retrieval node cannot execute in two parallel groups")
        expected_executed = {
            item.node_id for item in self.nodes if item.status != "skipped"
        }
        if set(executed) != expected_executed:
            raise ValueError("parallel groups must exactly name executed nodes")
        if self.returned_hits == 0 and self.stop_reason not in {
            "no_evidence", "required_node_failed",
        }:
            raise ValueError("an empty retrieval result cannot pass or be partial")
        if self.stop_reason == "no_evidence" and self.returned_hits != 0:
            raise ValueError("no_evidence requires an empty retrieval result")
        if self.stop_reason == "pass" and any(
            item.status == "failed" for item in self.nodes
        ):
            raise ValueError("a passing retrieval graph cannot contain failed nodes")
        if self.stop_reason == "partial" and not any(
            item.status == "failed" for item in self.nodes
        ):
            raise ValueError("a partial retrieval graph requires a failed node")
        if self.stop_reason == "required_node_failed" and not any(
            item.required and item.status != "succeeded" for item in self.nodes
        ):
            raise ValueError("required_node_failed requires failed required work")
        if self.stop_reason != "required_node_failed" and any(
            item.required and item.status != "succeeded" for item in self.nodes
        ):
            raise ValueError("failed required work must stop as required_node_failed")

    def as_dict(self) -> dict:
        return {
            "schemaVersion": "prepende-retrieval-graph-v1",
            "planId": self.plan_id,
            "planHash": self.plan_hash,
            "tenantId": self.tenant_id,
            "workspaceId": self.workspace_id,
            "stopReason": self.stop_reason,
            "nodes": [
                {
                    "nodeId": item.node_id,
                    "queryHash": item.query_hash,
                    "status": item.status,
                    "dependsOn": list(item.depends_on),
                    "runIf": item.run_if,
                    "required": item.required,
                    "retrieved": item.retrieved,
                    "accepted": item.accepted,
                    "rejected": item.rejected,
                    "outputHash": item.output_hash,
                    **({"error": item.error} if item.error else {}),
                }
                for item in self.nodes
            ],
            "parallelGroups": [list(group) for group in self.parallel_groups],
            "maxParallelism": self.max_parallelism,
            "maxObservedParallelism": self.max_observed_parallelism,
            "roundsUsed": self.rounds_used,
            "returnedHits": self.returned_hits,
            "duplicatesDropped": self.duplicates_dropped,
            "externalActions": [],
            "actionExecuted": False,
            "durableMemoryWrite": False,
        }
