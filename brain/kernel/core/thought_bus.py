"""Thought Bus — recursive pre-action coordination for Prepende.

This is the practical MVP of the "telepathy" layer: agents do not chat with
each other or own durable state. Prepende sends each agent the same structured
packet, collects structured imprints, fuses them, and decides whether to stop,
recurse, or require approval before any external action.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field, replace
import hashlib
import json
from pathlib import Path
import re
import time
import uuid
from typing import Any, Iterable, Literal

from kernel.contracts.meditation import (
    CommitIntent,
    EvidenceDigest,
    MeditationPolicy,
    MeditationReceipt,
)
from kernel.core.action_intent import looks_like_action_request
from kernel.core.meditation_bridge import (
    POLICY_ID as MEDITATION_POLICY_ID,
    DeterministicMeditationPolicy,
    build_meditation_input,
    meditation_input_hash,
    packet_hash,
    validate_meditation_resolution,
)
from prepende_brain.private_fs import secure_directory, secure_file

Status = Literal["ok", "needs_revision", "blocked"]
DecisionStatus = Literal["ready", "needs_revision", "blocked"]

DEFAULT_MAX_DEPTH = 2
DEFAULT_BUDGET = 4
MAX_PACKET_TEXT_CHARS = 8_000
MAX_PACKET_ITEMS = 64
MAX_PACKET_ITEM_CHARS = 1_000
NEGATIVE_TERMS = ("fail", "failure", "broken", "risk", "unsafe", "contradict", "conflict")


@dataclass(frozen=True)
class ThoughtPacket:
    """Shared pre-action state all sandboxed agents receive."""

    run_id: str
    workspace_id: str
    goal: str
    task: str
    constraints: list[str]
    memory_refs: list[str] = field(default_factory=list)
    source_refs: list[str] = field(default_factory=list)
    allowed_actions: list[str] = field(default_factory=list)
    budget: int = DEFAULT_BUDGET
    depth: int = 0
    max_depth: int = DEFAULT_MAX_DEPTH
    parent_run_id: str | None = None

    def validate(self) -> None:
        missing: list[str] = []
        if not self.run_id.strip():
            missing.append("run_id")
        if not self.workspace_id.strip():
            missing.append("workspace_id")
        if not self.goal.strip():
            missing.append("goal")
        if missing:
            raise ValueError(f"ThoughtPacket missing required field(s): {', '.join(missing)}")
        for field_name, text in (("goal", self.goal), ("task", self.task)):
            if not isinstance(text, str) or len(text) > MAX_PACKET_TEXT_CHARS:
                raise ValueError(f"ThoughtPacket {field_name} exceeds text budget")
        for field_name, values in (
            ("constraints", self.constraints),
            ("memory_refs", self.memory_refs),
            ("source_refs", self.source_refs),
            ("allowed_actions", self.allowed_actions),
        ):
            if not isinstance(values, list) or len(values) > MAX_PACKET_ITEMS:
                raise ValueError(f"ThoughtPacket {field_name} exceeds item budget")
            if any(not isinstance(item, str) or len(item) > MAX_PACKET_ITEM_CHARS for item in values):
                raise ValueError(f"ThoughtPacket {field_name} contains an oversized item")
        if self.budget < 1:
            raise ValueError("ThoughtPacket budget must be >= 1")
        if self.depth < 0:
            raise ValueError("ThoughtPacket depth must be >= 0")
        if self.max_depth < 0:
            raise ValueError("ThoughtPacket max_depth must be >= 0")
        if self.depth > self.max_depth:
            raise ValueError("ThoughtPacket depth cannot exceed max_depth")


@dataclass(frozen=True)
class AgentWorkResult:
    """Structured thought imprint returned by one sandboxed agent."""

    agent_id: str
    role: str
    status: Status
    confidence: float
    claims: list[str]
    evidence: list[str]
    risks: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    proposed_artifacts: list[dict[str, Any]] = field(default_factory=list)
    memory_candidates: list[dict[str, Any]] = field(default_factory=list)
    next_thoughts: list[str] = field(default_factory=list)
    external_action_requested: bool = False
    available: bool = True
    error: str | None = None
    packet_hash: str = ""
    model_provenance: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.agent_id.strip():
            raise ValueError("AgentWorkResult agent_id is required")
        if not self.role.strip():
            raise ValueError("AgentWorkResult role is required")
        if self.status not in {"ok", "needs_revision", "blocked"}:
            raise ValueError("AgentWorkResult status is invalid")
        if self.confidence < 0 or self.confidence > 1:
            raise ValueError("AgentWorkResult confidence must be between 0 and 1")
        if not isinstance(self.available, bool):
            raise ValueError("AgentWorkResult available must be boolean")
        if self.available and self.error:
            raise ValueError("available AgentWorkResult cannot include an error")


@dataclass(frozen=True)
class ThoughtReceipt:
    """Auditable receipt for one agent pass."""

    id: str
    run_id: str
    agent_id: str
    role: str
    status: Status
    confidence: float
    claim_count: int
    evidence_count: int
    risk_count: int
    blocker_count: int
    proposed_artifacts: list[dict[str, Any]]
    memory_candidates: list[dict[str, Any]]
    external_action_requested: bool
    available: bool = True
    error: str | None = None
    packet_hash: str = ""
    model_provenance: dict[str, Any] = field(default_factory=dict)
    action_executed: bool = False
    external_actions: str = "none"


@dataclass(frozen=True)
class FusionDecision:
    """Prepende's single decision after fusing agent imprints."""

    status: DecisionStatus
    confidence: float
    summary: str
    agreement: list[str]
    conflicts: list[str]
    risks: list[str]
    blockers: list[str]
    next_action: str
    recurse: bool
    reason: str
    external_actions: str = "none"
    approval_required: bool = False


@dataclass(frozen=True)
class ThoughtBusRun:
    """Public API payload for a completed Thought Bus run."""

    workspace_id: str
    run_id: str
    mode: str
    status: str
    goal: str
    depth: int
    max_depth: int
    thought_receipts: list[ThoughtReceipt]
    fusion_decision: FusionDecision
    memory_updates: list[dict[str, Any]]
    approval_required: bool
    external_actions: str = "none"
    action_executed: bool = False
    meditation_receipt: MeditationReceipt | None = None
    commit_intent: CommitIntent | None = None
    agent_mode: str = "deterministic"
    model_backed: bool = False
    packet_hash: str = ""
    evidence_digest: EvidenceDigest | None = None
    model_calls: int = 0
    model_call_budget: int = 0
    semantic_meditation: bool = False


def _now_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _contains_external_action(text: str) -> bool:
    """Compatibility wrapper around the shared request-aware policy."""

    return looks_like_action_request(text)


def _contains_negative_signal(text: str) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in NEGATIVE_TERMS)


def _stable_artifact_path(run_id: str, role: str) -> str:
    digest = hashlib.sha256(f"{run_id}:{role}".encode()).hexdigest()[:10]
    return f"sandbox/{run_id}/{role}-{digest}.md"


class MockThoughtAgent:
    """Deterministic sandbox agent for the MVP and smoke tests."""

    def __init__(self, agent_id: str, role: str) -> None:
        self.agent_id = agent_id
        self.role = role

    def run(self, packet: ThoughtPacket) -> AgentWorkResult:
        packet.validate()
        goal = packet.goal.strip()
        external = _contains_external_action(goal)
        risky = _contains_negative_signal(goal)

        if self.role == "scout":
            return AgentWorkResult(
                agent_id=self.agent_id,
                role=self.role,
                status="ok",
                confidence=0.82,
                claims=[
                    "Prepende should preserve the goal, workspace, constraints, and provenance before action.",
                    "The source of truth is the shared ThoughtPacket, not agent-to-agent chat.",
                ],
                evidence=[
                    f"workspace:{packet.workspace_id}",
                    f"memory_refs:{len(packet.memory_refs)}",
                    f"source_refs:{len(packet.source_refs)}",
                ],
                risks=["External action language detected."] if external else [],
                next_thoughts=["Ask verifier to check approval and stop conditions."],
                external_action_requested=external,
            )
        if self.role == "builder":
            status: Status = "needs_revision" if risky else "ok"
            confidence = 0.58 if risky else 0.78
            return AgentWorkResult(
                agent_id=self.agent_id,
                role=self.role,
                status=status,
                confidence=confidence,
                claims=["A sandboxed artifact can be proposed without committing durable changes."],
                evidence=[f"artifact:{_stable_artifact_path(packet.run_id, self.role)}"],
                risks=["Goal text contains negative or failure language; run a repair pass."] if risky else [],
                proposed_artifacts=[{
                    "type": "sandbox_proposal",
                    "path": _stable_artifact_path(packet.run_id, self.role),
                    "description": "Draft implementation proposal; Prepende must inspect before merge.",
                    "mergeAllowed": False,
                }],
                memory_candidates=[{
                    "content": f"Candidate learning from Thought Bus goal: {goal[:160]}",
                    "status": "candidate",
                    "requiresAssess": True,
                    "source": "thought_bus.builder",
                }],
                next_thoughts=["If verifier confidence is low, recurse with a narrower task."],
                external_action_requested=external,
            )
        if self.role == "reviewer":
            return AgentWorkResult(
                agent_id=self.agent_id,
                role=self.role,
                status="needs_revision" if external else "ok",
                confidence=0.62 if external else 0.8,
                claims=["Agents must not write durable memory or execute connector actions directly."],
                evidence=["policy:externalActions=none", "policy:Prepende owns final receipt"],
                risks=["Approval is required before any external action."] if external else [],
                blockers=["external_action_requires_approval"] if external else [],
                next_thoughts=["Keep final decision centralized in Prepende."],
                external_action_requested=external,
            )
        # verifier
        if external:
            status = "blocked"
            confidence = 0.9
            blockers = ["external_action_requires_approval"]
            risks = ["External action attempt was blocked; no connector call executed."]
        elif risky and packet.depth == 0:
            status = "needs_revision"
            confidence = 0.45
            blockers = []
            risks = ["Low confidence on first pass; recurse once with tighter task."]
        else:
            status = "ok"
            confidence = 0.86
            blockers = []
            risks = []
        return AgentWorkResult(
            agent_id=self.agent_id,
            role=self.role,
            status=status,
            confidence=confidence,
            claims=["Verifier checked stop conditions, approval gate, and confidence threshold."],
            evidence=[
                f"depth:{packet.depth}",
                f"max_depth:{packet.max_depth}",
                "externalActions:none",
            ],
            risks=risks,
            blockers=blockers,
            next_thoughts=[] if status == "ok" else ["Recurse only if budget and max_depth allow it."],
            external_action_requested=external,
        )


class LocalArtifactSandboxRunner:
    """Dispatches an agent and stores inspectable output in an isolated folder."""

    def __init__(self, root: str | Path = ".engram_sandbox/thought_bus") -> None:
        self.root = Path(root)

    def dispatch(self, packet: ThoughtPacket, agent: Any) -> AgentWorkResult:
        packet.validate()
        result = agent.run(packet)
        result.validate()

        return self.store(packet, result)

    def store(self, packet: ThoughtPacket, result: AgentWorkResult) -> AgentWorkResult:
        """Persist one already-computed imprint in the existing local sandbox."""

        packet.validate()
        result.validate()
        result = replace(result, packet_hash=packet_hash(packet))

        sandbox_dir = self._sandbox_dir(packet, result)
        secure_directory(sandbox_dir)

        packet_path = sandbox_dir / "packet.json"
        result_path = sandbox_dir / "result.json"
        summary_path = sandbox_dir / "summary.md"
        public_dir = self._public_dir(packet, result)

        packet_path.write_text(json.dumps(asdict(packet), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        result_path.write_text(json.dumps(asdict(result), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        summary_path.write_text(self._summary(packet, result), encoding="utf-8")
        for path in (packet_path, result_path, summary_path):
            secure_file(path, required=True)

        artifact = {
            "type": "sandbox_result",
            "path": f"{public_dir}/result.json",
            "summaryPath": f"{public_dir}/summary.md",
            "packetPath": f"{public_dir}/packet.json",
            "description": f"Isolated {result.role} agent output; Prepende must inspect before merge.",
            "runner": "local_artifact_sandbox",
            "isolation": "local_artifact_directory",
            "mergeAllowed": False,
            "durableWrite": False,
        }
        return AgentWorkResult(
            agent_id=result.agent_id,
            role=result.role,
            status=result.status,
            confidence=result.confidence,
            claims=result.claims,
            evidence=result.evidence + [f"sandbox_result:{public_dir}/result.json"],
            risks=result.risks,
            blockers=result.blockers,
            proposed_artifacts=result.proposed_artifacts + [artifact],
            memory_candidates=result.memory_candidates,
            next_thoughts=result.next_thoughts,
            external_action_requested=result.external_action_requested,
            available=result.available,
            error=result.error,
            packet_hash=result.packet_hash,
            model_provenance=deepcopy(result.model_provenance),
        )

    def _sandbox_dir(self, packet: ThoughtPacket, result: AgentWorkResult) -> Path:
        role = re.sub(r"[^a-zA-Z0-9_.-]+", "-", result.role).strip("-") or "agent"
        digest = hashlib.sha256(f"{packet.run_id}:{result.agent_id}:{result.role}:{packet.depth}".encode()).hexdigest()[:10]
        return self.root / packet.run_id / f"depth-{packet.depth}" / f"{role}-{digest}"

    def _public_dir(self, packet: ThoughtPacket, result: AgentWorkResult) -> str:
        role = re.sub(r"[^a-zA-Z0-9_.-]+", "-", result.role).strip("-") or "agent"
        digest = hashlib.sha256(f"{packet.run_id}:{result.agent_id}:{result.role}:{packet.depth}".encode()).hexdigest()[:10]
        return f"sandbox://thought-bus/{packet.run_id}/depth-{packet.depth}/{role}-{digest}"

    def _summary(self, packet: ThoughtPacket, result: AgentWorkResult) -> str:
        lines = [
            f"# Sandbox Result: {result.role}",
            "",
            f"- runId: {packet.run_id}",
            f"- workspaceId: {packet.workspace_id}",
            f"- agentId: {result.agent_id}",
            f"- status: {result.status}",
            f"- confidence: {result.confidence:.3f}",
            "- externalActions: none",
            "- actionExecuted: false",
            "- mergeAllowed: false",
            "- durableWrite: false",
            "",
            "## Claims",
            *[f"- {claim}" for claim in result.claims],
            "",
            "## Evidence",
            *[f"- {item}" for item in result.evidence],
        ]
        if result.risks:
            lines.extend(["", "## Risks", *[f"- {risk}" for risk in result.risks]])
        if result.blockers:
            lines.extend(["", "## Blockers", *[f"- {blocker}" for blocker in result.blockers]])
        return "\n".join(lines) + "\n"


class ThoughtBusOrchestrator:
    """Runs the MVP recursive thought loop with deterministic sandbox agents."""

    def __init__(
        self,
        agents: Iterable[MockThoughtAgent] | None = None,
        sandbox_runner: LocalArtifactSandboxRunner | None = None,
        meditation_policy: MeditationPolicy | None = None,
    ) -> None:
        self.agents = list(agents or [
            MockThoughtAgent("thought_scout", "scout"),
            MockThoughtAgent("thought_builder", "builder"),
            MockThoughtAgent("thought_reviewer", "reviewer"),
            MockThoughtAgent("thought_verifier", "verifier"),
        ])
        self.sandbox_runner = sandbox_runner or LocalArtifactSandboxRunner()
        self.meditation_policy = meditation_policy

    def run(
        self,
        *,
        workspace_id: str,
        goal: str,
        memory_refs: list[str] | None = None,
        source_refs: list[str] | None = None,
        max_depth: int = DEFAULT_MAX_DEPTH,
        budget: int = DEFAULT_BUDGET,
        constraints: list[str] | None = None,
        parent_run_id: str | None = None,
    ) -> ThoughtBusRun:
        run_id = _now_id("tb")
        packet = ThoughtPacket(
            run_id=run_id,
            workspace_id=workspace_id,
            goal=goal,
            task="coordinate sandboxed agents before action",
            constraints=constraints or [
                "Prepende owns final decision.",
                "Agents cannot write durable memory directly.",
                "Agents cannot execute external actions.",
                "externalActions must remain none.",
            ],
            memory_refs=memory_refs or [],
            source_refs=source_refs or [],
            allowed_actions=["think", "propose_artifact", "propose_memory_candidate", "verify"],
            budget=budget,
            depth=0,
            max_depth=max_depth,
            parent_run_id=parent_run_id,
        )
        return self._run_packet(packet)

    def _run_packet(self, packet: ThoughtPacket) -> ThoughtBusRun:
        packet.validate()
        results = [self.sandbox_runner.dispatch(packet, agent) for agent in self.agents]
        for result in results:
            result.validate()
        receipts = [self._receipt(packet.run_id, result) for result in results]
        decision = self._fuse(packet, results)

        if decision.recurse and packet.depth < packet.max_depth and packet.budget > 1:
            child = ThoughtPacket(
                run_id=packet.run_id,
                workspace_id=packet.workspace_id,
                goal=f"{packet.goal}\n\nRepair focus: {decision.reason}"[:MAX_PACKET_TEXT_CHARS],
                task="recursive repair pass before final action",
                constraints=packet.constraints,
                memory_refs=packet.memory_refs,
                source_refs=packet.source_refs,
                allowed_actions=packet.allowed_actions,
                budget=packet.budget - 1,
                depth=packet.depth + 1,
                max_depth=packet.max_depth,
                parent_run_id=packet.run_id,
            )
            child_run = self._run_packet(child)
            return ThoughtBusRun(
                workspace_id=child_run.workspace_id,
                run_id=child_run.run_id,
                mode="thought_bus",
                status=child_run.status,
                goal=packet.goal,
                depth=child_run.depth,
                max_depth=packet.max_depth,
                thought_receipts=receipts + child_run.thought_receipts,
                fusion_decision=child_run.fusion_decision,
                memory_updates=child_run.memory_updates,
                approval_required=child_run.approval_required,
                external_actions="none",
                action_executed=False,
                meditation_receipt=child_run.meditation_receipt,
                commit_intent=child_run.commit_intent,
                agent_mode=child_run.agent_mode,
                model_backed=child_run.model_backed,
                packet_hash=child_run.packet_hash,
                evidence_digest=child_run.evidence_digest,
                model_calls=child_run.model_calls,
                model_call_budget=child_run.model_call_budget,
                semantic_meditation=child_run.semantic_meditation,
            )

        memory_updates: list[dict[str, Any]] = []
        for result in results:
            memory_updates.extend(result.memory_candidates)
        for update in memory_updates:
            update.setdefault("status", "candidate")
            update.setdefault("requiresAssess", True)
            update.setdefault("durableWrite", False)

        meditation_receipt: MeditationReceipt | None = None
        commit_intent: CommitIntent | None = None
        meditation_input = build_meditation_input(
            packet=packet,
            results=results,
            receipts=receipts,
            decision=decision,
        )
        if self.meditation_policy is not None:
            try:
                # Frozen dataclasses are shallow: candidate params are dictionaries.
                # Give an injected policy a detached copy, then detach its output
                # and validate it against the untouched trusted input.
                policy_input = deepcopy(meditation_input)
                meditation_resolution = deepcopy(self.meditation_policy.resolve(policy_input))
                resolution_error = validate_meditation_resolution(
                    meditation_input,
                    meditation_resolution,
                    allow_approval_intent=False,
                )
                if resolution_error:
                    meditation_receipt = MeditationReceipt(
                        policy_id=MEDITATION_POLICY_ID,
                        status="blocked",
                        reason=resolution_error,
                        input_receipt_ids=tuple(receipt.id for receipt in receipts),
                        candidate_count=len(meditation_input.candidates),
                        selected_intent_id=None,
                        selected_candidate_id=None,
                        input_hash=meditation_input_hash(meditation_input),
                        provider="none",
                        auth_lane="none",
                        requested_model="deterministic",
                        resolved_model=None,
                        fallback_used=False,
                        latency_ms=0,
                        model_call_count=0,
                    )
                else:
                    meditation_receipt = meditation_resolution.receipt
                    commit_intent = meditation_resolution.commit_intent
            except Exception as exc:
                # A policy bug must fail closed without erasing the Thought Bus
                # receipt or turning a proposal into an action.
                meditation_receipt = MeditationReceipt(
                    policy_id=MEDITATION_POLICY_ID,
                    status="blocked",
                    reason=f"policy_error:{type(exc).__name__}",
                    input_receipt_ids=tuple(receipt.id for receipt in receipts),
                    candidate_count=len(meditation_input.candidates),
                    selected_intent_id=None,
                    selected_candidate_id=None,
                    input_hash=meditation_input_hash(meditation_input),
                    provider="none",
                    auth_lane="none",
                    requested_model="deterministic",
                    resolved_model=None,
                    fallback_used=False,
                    latency_ms=0,
                    model_call_count=0,
                )

        return ThoughtBusRun(
            workspace_id=packet.workspace_id,
            run_id=packet.run_id,
            mode="thought_bus",
            status=decision.status,
            goal=packet.goal,
            depth=packet.depth,
            max_depth=packet.max_depth,
            thought_receipts=receipts,
            fusion_decision=decision,
            memory_updates=memory_updates,
            approval_required=decision.approval_required,
            external_actions="none",
            action_executed=False,
            meditation_receipt=meditation_receipt,
            commit_intent=commit_intent,
            agent_mode="deterministic",
            model_backed=False,
            packet_hash=packet_hash(packet),
            evidence_digest=meditation_input.evidence_digest,
            model_calls=0,
            model_call_budget=0,
            semantic_meditation=False,
        )

    def _receipt(self, run_id: str, result: AgentWorkResult) -> ThoughtReceipt:
        return ThoughtReceipt(
            id=_now_id("tr"),
            run_id=run_id,
            agent_id=result.agent_id,
            role=result.role,
            status=result.status,
            confidence=round(result.confidence, 3),
            claim_count=len(result.claims),
            evidence_count=len(result.evidence),
            risk_count=len(result.risks),
            blocker_count=len(result.blockers),
            proposed_artifacts=result.proposed_artifacts,
            memory_candidates=result.memory_candidates,
            external_action_requested=result.external_action_requested,
            available=result.available,
            error=result.error,
            packet_hash=result.packet_hash,
            model_provenance=deepcopy(result.model_provenance),
            action_executed=False,
            external_actions="none",
        )

    def _fuse(self, packet: ThoughtPacket, results: list[AgentWorkResult]) -> FusionDecision:
        available_results = [result for result in results if result.available]
        unavailable_results = [result for result in results if not result.available]
        confidences = [r.confidence for r in available_results]
        avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0
        blockers = sorted({b for r in available_results for b in r.blockers})
        risks = sorted({risk for r in available_results for risk in r.risks})
        risks.extend(f"agent_unavailable:{result.agent_id}" for result in unavailable_results)
        risks = sorted(set(risks))
        claims = [claim for r in available_results for claim in r.claims]
        agreement = sorted({
            "Prepende owns final decision",
            "externalActions none",
            "durable memory candidates require Assess",
        })
        conflicts = sorted({risk for risk in risks if "conflict" in risk.lower() or "contradict" in risk.lower()})

        external = any(r.external_action_requested for r in available_results)
        conflict_detected = bool(conflicts)
        low_confidence = (
            avg_confidence < 0.68
            or any(r.status == "needs_revision" for r in available_results)
            or conflict_detected
        )
        blocked = (
            bool(blockers)
            or any(r.status == "blocked" for r in available_results)
            or external
        )
        can_recurse = low_confidence and not blocked and packet.depth < packet.max_depth and packet.budget > 1

        if not available_results:
            status = "blocked"
            blockers = ["all_model_agents_unavailable"]
            next_action = "Return the failure receipts for operator review; no external action was executed."
            reason = "No model-backed agent returned a valid imprint."
            approval_required = False
            can_recurse = False
        elif blocked:
            status: DecisionStatus = "blocked"
            next_action = "Create or review an approval request; no external action was executed."
            reason = "External action or blocker detected."
            approval_required = True
        elif can_recurse:
            status = "needs_revision"
            next_action = "Run one recursive repair pass with the same central Prepende state."
            reason = "Conflict, low verifier confidence, or an agent revision request requires repair."
            approval_required = False
        elif low_confidence:
            status = "needs_revision"
            next_action = "Stop at recursion/budget limit and return receipt for human review."
            reason = "Conflict or low confidence remains after recursion or budget limit."
            approval_required = False
        else:
            status = "ready"
            next_action = "Return fused result; keep memory updates as ASSESS candidates."
            reason = "Agents agree enough for an Prepende-owned decision."
            approval_required = False

        summary = (
            f"Fused {len(available_results)} available sandboxed agent imprints for workspace "
            f"{packet.workspace_id}; {len(unavailable_results)} unavailable. Claims observed: "
            f"{len(claims)}. External actions executed: none."
        )
        return FusionDecision(
            status=status,
            confidence=round(avg_confidence, 3),
            summary=summary,
            agreement=agreement,
            conflicts=conflicts,
            risks=risks,
            blockers=blockers,
            next_action=next_action,
            recurse=can_recurse,
            reason=reason,
            external_actions="none",
            approval_required=approval_required,
        )


def run_thought_bus(**kwargs: Any) -> dict[str, Any]:
    """Convenience API: run the MVP orchestrator and return JSON-safe output."""
    started = time.time()
    sandbox_root = kwargs.pop("sandbox_root", None)
    meditation_policy = kwargs.pop("meditation_policy", None)
    meditate = kwargs.pop("meditate", False) is True
    if meditate and meditation_policy is None:
        meditation_policy = DeterministicMeditationPolicy()
    orchestrator = ThoughtBusOrchestrator(
        sandbox_runner=LocalArtifactSandboxRunner(sandbox_root) if sandbox_root else None,
        meditation_policy=meditation_policy,
    )
    run = orchestrator.run(**kwargs)
    out = asdict(run)
    out["elapsedMs"] = int((time.time() - started) * 1000)
    return out
