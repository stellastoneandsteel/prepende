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
    "identity_mismatch",
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
    "identity_mismatch",
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
class RetrievalIdentity:
    """Server-owned binding between a logical scope and one physical index.

    The hashes deliberately reveal neither a filesystem path nor corpus data.
    ``index_revision`` changes when the certified index snapshot changes, while
    the corpus and index hashes identify the physical handles that produced it.
    """

    tenant_id: str
    workspace_id: str
    scope_id: str
    corpus_root_hash: str
    index_path_hash: str
    index_revision: str
    source_files: int
    chunks: int

    def __post_init__(self) -> None:
        for label in ("tenant_id", "workspace_id", "scope_id"):
            object.__setattr__(self, label, _stable_id(label, getattr(self, label)))
        for label in ("corpus_root_hash", "index_path_hash", "index_revision"):
            value = getattr(self, label)
            if not isinstance(value, str) or not _SHA256.fullmatch(value):
                raise ValueError(f"{label} must be a sha256 content hash")
        for label in ("source_files", "chunks"):
            value = getattr(self, label)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{label} must be a non-negative integer")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "RetrievalIdentity":
        if not isinstance(value, Mapping):
            raise TypeError("retrieval identity must be an object")
        aliases = {
            "tenant_id": ("tenantId", "tenant_id"),
            "workspace_id": ("workspaceId", "workspace_id"),
            "scope_id": ("scopeId", "scope_id"),
            "corpus_root_hash": ("corpusRootHash", "corpus_root_hash"),
            "index_path_hash": ("indexPathHash", "index_path_hash"),
            "index_revision": ("indexRevision", "index_revision"),
            "source_files": ("sourceFiles", "source_files"),
            "chunks": ("chunks",),
        }
        parsed: dict[str, Any] = {}
        for target, names in aliases.items():
            present = [value[name] for name in names if name in value]
            if not present:
                raise ValueError(f"retrieval identity is missing {names[0]}")
            if any(item != present[0] for item in present[1:]):
                raise ValueError(f"retrieval identity aliases disagree for {names[0]}")
            parsed[target] = present[0]
        return cls(**parsed)

    @property
    def identity_hash(self) -> str:
        return canonical_hash(self)

    def as_dict(self) -> dict[str, Any]:
        return {
            "tenantId": self.tenant_id,
            "workspaceId": self.workspace_id,
            "scopeId": self.scope_id,
            "corpusRootHash": self.corpus_root_hash,
            "indexPathHash": self.index_path_hash,
            "indexRevision": self.index_revision,
            "sourceFiles": self.source_files,
            "chunks": self.chunks,
        }


@dataclass(frozen=True)
class RetrievalSearchResult:
    """One search response bound to the physical snapshot that produced it."""

    hits: tuple[Any, ...]
    identity: RetrievalIdentity

    def __post_init__(self) -> None:
        if not isinstance(self.hits, tuple):
            raise TypeError("retrieval search hits must be a tuple")
        if not isinstance(self.identity, RetrievalIdentity):
            raise TypeError("retrieval search identity is required")


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
    max_content_chars: int = 128_000
    max_metadata_bytes: int = 64_000
    identity: RetrievalIdentity | None = None

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
        if (
            isinstance(self.max_content_chars, bool)
            or not isinstance(self.max_content_chars, int)
            or not 1 <= self.max_content_chars <= 512_000
        ):
            raise ValueError("max_content_chars must be between one and 512000")
        if (
            isinstance(self.max_metadata_bytes, bool)
            or not isinstance(self.max_metadata_bytes, int)
            or not 1 <= self.max_metadata_bytes <= 256_000
        ):
            raise ValueError("max_metadata_bytes must be between one and 256000")
        if self.identity is not None:
            if not isinstance(self.identity, RetrievalIdentity):
                raise TypeError("identity must be a RetrievalIdentity")
            if (
                self.identity.tenant_id != self.tenant_id
                or self.identity.workspace_id != self.workspace_id
            ):
                raise ValueError("retrieval plan identity does not match its logical scope")

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
    requested_k: int = 5
    invalid_rejected: int = 0
    provenance_rejected: int = 0
    budget_rejected: int = 0
    identity_verified: bool = False
    executed: bool | None = None

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
        for value in (
            self.retrieved,
            self.accepted,
            self.rejected,
            self.invalid_rejected,
            self.provenance_rejected,
            self.budget_rejected,
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError("retrieval counts must be non-negative integers")
        if self.accepted + self.rejected != self.retrieved:
            raise ValueError("accepted plus rejected must equal retrieved")
        if (
            self.invalid_rejected
            + self.provenance_rejected
            + self.budget_rejected
            > self.rejected
        ):
            raise ValueError("rejection categories cannot exceed rejected hits")
        if (
            isinstance(self.requested_k, bool)
            or not isinstance(self.requested_k, int)
            or not 1 <= self.requested_k <= 25
        ):
            raise ValueError("requested_k must be between one and 25")
        if not isinstance(self.identity_verified, bool):
            raise TypeError("identity_verified must be boolean")
        if self.executed is None:
            object.__setattr__(self, "executed", self.status != "skipped")
        if not isinstance(self.executed, bool):
            raise TypeError("executed must be boolean")
        if self.status == "succeeded" and not self.executed:
            raise ValueError("a succeeded node must have executed")
        if self.status == "skipped" and self.executed:
            raise ValueError("a skipped node cannot have executed")
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
    budget_dropped: int = 0
    returned_content_chars: int = 0
    returned_metadata_bytes: int = 0
    branches_covered: tuple[str, ...] = ()
    branches_starved: tuple[str, ...] = ()
    retrieval_identity: RetrievalIdentity | None = None
    identity_verified: bool = False

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
        if not 0 <= self.max_observed_parallelism <= self.max_parallelism <= 8:
            raise ValueError("invalid observed retrieval parallelism")
        if not 0 <= self.rounds_used <= 2:
            raise ValueError("invalid retrieval rounds_used")
        if (
            self.returned_hits < 0
            or self.duplicates_dropped < 0
            or self.budget_dropped < 0
            or self.returned_content_chars < 0
            or self.returned_metadata_bytes < 0
        ):
            raise ValueError("retrieval graph counts must be non-negative")
        if not isinstance(self.identity_verified, bool):
            raise TypeError("identity_verified must be boolean")
        if self.retrieval_identity is not None:
            if not isinstance(self.retrieval_identity, RetrievalIdentity):
                raise TypeError("retrieval_identity must be a RetrievalIdentity")
            if (
                self.retrieval_identity.tenant_id != self.tenant_id
                or self.retrieval_identity.workspace_id != self.workspace_id
            ):
                raise ValueError("receipt identity does not match its logical scope")
        elif self.identity_verified:
            raise ValueError("identity_verified requires a retrieval identity")
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
        expected_executed = {item.node_id for item in self.nodes if item.executed}
        if set(executed) != expected_executed:
            raise ValueError("parallel groups must exactly name executed nodes")
        if not executed and (self.rounds_used != 0 or self.max_observed_parallelism != 0):
            raise ValueError("a graph with no executed nodes must report zero runtime work")
        if executed and self.rounds_used == 0:
            raise ValueError("an executed graph must report at least one round")
        if self.returned_hits == 0 and self.stop_reason not in {
            "no_evidence", "required_node_failed", "identity_mismatch",
        }:
            raise ValueError("an empty retrieval result cannot pass or be partial")
        if self.stop_reason == "no_evidence" and self.returned_hits != 0:
            raise ValueError("no_evidence requires an empty retrieval result")
        if self.stop_reason == "pass" and any(
            item.status == "failed" for item in self.nodes
        ):
            raise ValueError("a passing retrieval graph cannot contain failed nodes")
        if self.stop_reason == "partial" and not (
            any(item.status == "failed" for item in self.nodes)
            or bool(self.branches_starved)
        ):
            raise ValueError("a partial retrieval graph requires failure or budget starvation")
        if self.stop_reason == "required_node_failed" and not any(
            item.required and item.status != "succeeded" for item in self.nodes
        ):
            raise ValueError("required_node_failed requires failed required work")
        if self.stop_reason not in {"required_node_failed", "identity_mismatch"} and any(
            item.required and item.status != "succeeded" for item in self.nodes
        ):
            raise ValueError("failed required work must stop as required_node_failed")
        if self.stop_reason == "identity_mismatch":
            if self.returned_hits != 0:
                raise ValueError("identity_mismatch cannot return hits")
            if not any(
                item.error == "retrieval_identity_mismatch"
                or item.provenance_rejected > 0
                for item in self.nodes
            ):
                raise ValueError("identity_mismatch requires rejected identity evidence")
            if self.identity_verified:
                raise ValueError("identity_mismatch cannot be identity verified")
        for label, values in (
            ("branches_covered", self.branches_covered),
            ("branches_starved", self.branches_starved),
        ):
            if not isinstance(values, tuple):
                raise TypeError(f"{label} must be a tuple")
            if len(values) != len(set(values)) or set(values) - known:
                raise ValueError(f"{label} contains invalid node ids")
        if set(self.branches_covered) & set(self.branches_starved):
            raise ValueError("covered and starved branches must be disjoint")
        if self.stop_reason == "pass" and self.branches_starved:
            raise ValueError("a passing graph cannot starve a successful branch")

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
                    "requestedK": item.requested_k,
                    "invalidRejected": item.invalid_rejected,
                    "provenanceRejected": item.provenance_rejected,
                    "budgetRejected": item.budget_rejected,
                    "unclassifiedRejected": (
                        item.rejected
                        - item.invalid_rejected
                        - item.provenance_rejected
                        - item.budget_rejected
                    ),
                    "identityVerified": item.identity_verified,
                    "executed": item.executed,
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
            "budgetDropped": self.budget_dropped,
            "returnedContentChars": self.returned_content_chars,
            "returnedMetadataBytes": self.returned_metadata_bytes,
            "branchesCovered": list(self.branches_covered),
            "branchesStarved": list(self.branches_starved),
            "retrievalIdentity": (
                self.retrieval_identity.as_dict()
                if self.retrieval_identity is not None else None
            ),
            "identityVerified": self.identity_verified,
            "externalActions": [],
            "actionExecuted": False,
            "durableMemoryWrite": False,
        }
