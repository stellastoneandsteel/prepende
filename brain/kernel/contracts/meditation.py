"""Typed contract for the Thought Bus meditation boundary.

Meditation is a commitment policy, not an execution lane.  It receives the
bounded result of a Thought Bus run and may return zero or one proposed intent.
It cannot call tools, write memory, stage approvals, or execute the intent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol


IntentKind = Literal["return_fusion", "artifact_proposal", "registered_action"]
FusionStatus = Literal["ready", "needs_revision", "blocked"]
MeditationStatus = Literal["proposed", "abstained", "blocked"]


@dataclass(frozen=True)
class IntentCandidate:
    """One grounded option the meditation policy may select."""

    kind: IntentKind
    target_ref: str
    summary: str
    rationale: str
    evidence_refs: tuple[str, ...] = ()
    params: dict[str, Any] = field(default_factory=dict)
    approval_required: bool = False
    # Assigned by the kernel when a legacy caller omits it.  Keeping this
    # trailing preserves the original positional contract while new inputs
    # carry an explicit, stable id.
    id: str = ""


@dataclass(frozen=True)
class EvidenceDigestEntry:
    """Bounded, provenance-linked evidence from one final-leaf imprint."""

    receipt_id: str
    agent_id: str
    role: str
    packet_hash: str
    claims: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    risks: tuple[str, ...]
    blockers: tuple[str, ...]
    candidate_ids: tuple[str, ...]


@dataclass(frozen=True)
class EvidenceDigest:
    """The only agent-authored text semantic meditation may inspect."""

    packet_hash: str
    entries: tuple[EvidenceDigestEntry, ...]


@dataclass(frozen=True)
class MeditationInput:
    """The complete, bounded input to a meditation policy."""

    run_id: str
    workspace_id: str
    fusion_status: FusionStatus
    fusion_confidence: float
    fusion_reason: str
    blockers: tuple[str, ...]
    input_receipt_ids: tuple[str, ...]
    candidates: tuple[IntentCandidate, ...]
    # Added for semantic meditation. Defaults keep older deterministic
    # callers source-compatible; the kernel normalizes them before validation.
    conflicts: tuple[str, ...] = ()
    evidence_digest: EvidenceDigest | None = None
    packet_hash: str = ""


@dataclass(frozen=True)
class CommitIntent:
    """A proposed commitment.  This is never an executed action."""

    id: str
    run_id: str
    workspace_id: str
    kind: IntentKind
    target_ref: str
    summary: str
    rationale: str
    evidence_refs: tuple[str, ...]
    params: dict[str, Any]
    confidence: float
    approval_required: bool
    state: Literal["proposed"] = "proposed"


@dataclass(frozen=True)
class MeditationReceipt:
    """Auditable proof that meditation proposed, abstained, or blocked."""

    policy_id: str
    status: MeditationStatus
    reason: str
    input_receipt_ids: tuple[str, ...]
    candidate_count: int
    selected_intent_id: str | None
    # Existing callers may still construct the pre-provenance receipt shape.
    # Kernel-created receipts populate these fields explicitly.
    external_actions: Literal["none"] = "none"
    action_executed: Literal[False] = False
    durable_write: Literal[False] = False
    selected_candidate_id: str | None = None
    input_hash: str = ""
    provider: str = "none"
    auth_lane: str = "none"
    requested_model: str = "deterministic"
    resolved_model: str | None = None
    fallback_used: bool = False
    latency_ms: int = 0
    model_call_count: int = 0


@dataclass(frozen=True)
class MeditationResolution:
    receipt: MeditationReceipt
    commit_intent: CommitIntent | None


class MeditationPolicy(Protocol):
    """Swappable policy for reducing a Thought Bus result to 0..1 intent."""

    def resolve(self, value: MeditationInput) -> MeditationResolution:
        ...


class AsyncMeditationPolicy(Protocol):
    """Async policy for an explicit, bounded model-backed meditation pass."""

    async def resolve(self, value: MeditationInput) -> MeditationResolution:
        ...
