"""Pure Thought Bus -> meditation bridge.

The bridge gives Engram a mechanical version of "sit before you commit": it
looks only at the final, bounded Thought Bus state and returns zero or one
proposed CommitIntent.  It deliberately has no gateway, connector, memory,
workspace, filesystem, or approval-store dependency.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass, replace
import hashlib
import json
import re
from typing import Any, Callable, Iterable

from kernel.contracts.meditation import (
    CommitIntent,
    EvidenceDigest,
    EvidenceDigestEntry,
    IntentCandidate,
    MeditationInput,
    MeditationReceipt,
    MeditationResolution,
    MeditationStatus,
)


POLICY_ID = "thought-bus-meditation-v1"
MAX_CANDIDATES = 16
MAX_RECEIPTS = 64
MAX_BLOCKERS = 32
MAX_CONFLICTS = 32
MAX_TEXT_CHARS = 2_000
MAX_PARAMS_BYTES = 4_096
MAX_DIGEST_ENTRIES = 16
MAX_DIGEST_ITEMS = 12
MAX_DIGEST_TEXT_CHARS = 600
_KINDS = {"return_fusion", "artifact_proposal", "registered_action"}
_ARTIFACT_TARGET_PREFIXES = ("artifact://", "workspace://", "proposal://")
_SENSITIVE_PATTERNS = (
    re.compile(r"(?i)\b(?:bearer|basic)\s+[a-z0-9._~+/=-]{12,}"),
    re.compile(r"(?i)\b(?:api[_-]?key|secret|token|password)\s*[:=]\s*[^\s,;]{8,}"),
    re.compile(r"\b(?:sk|pk)_[a-zA-Z0-9_-]{12,}\b"),
)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def packet_hash(packet: Any) -> str:
    """Return the stable hash binding every imprint to one shared packet."""

    if is_dataclass(packet):
        payload = asdict(packet)
    elif isinstance(packet, dict):
        payload = packet
    else:
        raise TypeError("packet must be a dataclass or dictionary")
    return hashlib.sha256(_canonical_json(payload).encode()).hexdigest()


def _legacy_input_hash(value: MeditationInput) -> str:
    """Return a stable digest for pre-digest direct callers.

    The production Thought Bus path always supplies the real ThoughtPacket
    hash.  A small number of older deterministic callers construct
    MeditationInput directly, so they get a typed, non-secret compatibility
    digest rather than an unbound empty value.
    """

    payload = {
        "runId": str(value.run_id),
        "workspaceId": str(value.workspace_id),
        "receiptIds": [str(item) for item in value.input_receipt_ids],
        "candidateCount": len(value.candidates),
    }
    return hashlib.sha256(_canonical_json(payload).encode()).hexdigest()


def candidate_id(
    *,
    run_id: str,
    kind: str,
    target_ref: str,
    evidence_refs: Iterable[str],
) -> str:
    payload = {
        "runId": run_id,
        "kind": kind,
        "targetRef": target_ref,
        "evidenceRefs": list(evidence_refs),
    }
    return "mc_" + hashlib.sha256(_canonical_json(payload).encode()).hexdigest()[:20]


def normalize_meditation_input(value: MeditationInput) -> MeditationInput:
    """Fill only legacy omissions without granting callers new authority."""

    if not isinstance(value, MeditationInput):
        return value
    normalized_candidates: list[IntentCandidate] = []
    for candidate in value.candidates:
        if isinstance(candidate, IntentCandidate) and not candidate.id:
            normalized_candidates.append(replace(
                candidate,
                id=candidate_id(
                    run_id=str(value.run_id),
                    kind=str(candidate.kind),
                    target_ref=str(candidate.target_ref),
                    evidence_refs=tuple(str(ref) for ref in candidate.evidence_refs),
                ),
            ))
        else:
            normalized_candidates.append(candidate)
    digest = value.evidence_digest
    if digest is None:
        digest = EvidenceDigest(packet_hash=_legacy_input_hash(value), entries=())
    return replace(
        value,
        conflicts=tuple(value.conflicts or ()),
        candidates=tuple(normalized_candidates),
        evidence_digest=digest,
    )


def meditation_input_hash(value: MeditationInput) -> str:
    """Hash the complete trusted policy input for receipt verification."""

    normalized = normalize_meditation_input(value)
    try:
        encoded = _canonical_json(asdict(normalized))
    except (TypeError, ValueError):
        # Invalid candidate payloads still need a truthful blocked receipt. A
        # fallback representation is used only for hashing invalid input; it
        # is never presented to a model or promoted to an intent.
        encoded = json.dumps(
            asdict(normalized),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            default=repr,
            allow_nan=True,
        )
    return hashlib.sha256(encoded.encode()).hexdigest()


def _redact_untrusted_text(value: Any) -> str:
    text = str(value).strip()
    for pattern in _SENSITIVE_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text[:MAX_DIGEST_TEXT_CHARS]


def _digest_items(values: Iterable[Any]) -> tuple[str, ...]:
    return tuple(
        item
        for item in (_redact_untrusted_text(value) for value in list(values)[:MAX_DIGEST_ITEMS])
        if item
    )


def _intent_id(value: MeditationInput, candidate: IntentCandidate) -> str:
    payload = {
        "runId": value.run_id,
        "workspaceId": value.workspace_id,
        "kind": candidate.kind,
        "targetRef": candidate.target_ref,
        "params": candidate.params,
    }
    return "ci_" + hashlib.sha256(_canonical_json(payload).encode()).hexdigest()[:20]


def _receipt(
    value: MeditationInput,
    *,
    status: MeditationStatus,
    reason: str,
    selected_intent_id: str | None = None,
    selected_candidate_id: str | None = None,
    policy_id: str = POLICY_ID,
    provider: str = "none",
    auth_lane: str = "none",
    requested_model: str = "deterministic",
    resolved_model: str | None = None,
    fallback_used: bool = False,
    latency_ms: int = 0,
    model_call_count: int = 0,
) -> MeditationReceipt:
    return MeditationReceipt(
        policy_id=policy_id,
        status=status,
        reason=reason,
        input_receipt_ids=tuple(value.input_receipt_ids),
        candidate_count=len(value.candidates),
        selected_intent_id=selected_intent_id,
        selected_candidate_id=selected_candidate_id,
        input_hash=meditation_input_hash(value),
        provider=provider,
        auth_lane=auth_lane,
        requested_model=requested_model,
        resolved_model=resolved_model,
        fallback_used=fallback_used,
        latency_ms=latency_ms,
        model_call_count=model_call_count,
    )


def _stop(value: MeditationInput, *, status: MeditationStatus, reason: str) -> MeditationResolution:
    return MeditationResolution(
        receipt=_receipt(value, status=status, reason=reason),
        commit_intent=None,
    )


def _validation_error(value: MeditationInput) -> str | None:
    value = normalize_meditation_input(value)
    if not isinstance(value, MeditationInput):
        return "invalid_meditation_input"
    if not value.run_id.strip() or len(value.run_id) > 128:
        return "invalid_run_id"
    if not value.workspace_id.strip() or len(value.workspace_id) > 128:
        return "invalid_workspace_id"
    if value.fusion_status not in {"ready", "needs_revision", "blocked"}:
        return "invalid_fusion_status"
    try:
        confidence = float(value.fusion_confidence)
    except (TypeError, ValueError):
        return "invalid_fusion_confidence"
    if not 0.0 <= confidence <= 1.0:
        return "invalid_fusion_confidence"
    if not value.fusion_reason.strip() or len(value.fusion_reason) > MAX_TEXT_CHARS:
        return "invalid_fusion_reason"
    if len(value.blockers) > MAX_BLOCKERS or any(
        not blocker.strip() or len(blocker) > MAX_TEXT_CHARS for blocker in value.blockers
    ):
        return "invalid_blocker_payload"
    if len(value.conflicts) > MAX_CONFLICTS or any(
        not conflict.strip() or len(conflict) > MAX_TEXT_CHARS for conflict in value.conflicts
    ):
        return "invalid_conflict_payload"
    if len(value.input_receipt_ids) > MAX_RECEIPTS:
        return "receipt_budget_exceeded"
    if not value.input_receipt_ids or any(
        not ref.strip() or len(ref) > 128 for ref in value.input_receipt_ids
    ):
        return "invalid_receipt_reference"
    if len(set(value.input_receipt_ids)) != len(value.input_receipt_ids):
        return "duplicate_receipt_reference"
    if len(value.candidates) > MAX_CANDIDATES:
        return "candidate_budget_exceeded"

    valid_evidence = set(value.input_receipt_ids)
    candidate_ids = [candidate.id for candidate in value.candidates]
    if any(not item.strip() or len(item) > 128 for item in candidate_ids):
        return "invalid_candidate_id"
    if len(set(candidate_ids)) != len(candidate_ids):
        return "duplicate_candidate_id"
    for candidate in value.candidates:
        if candidate.kind not in _KINDS:
            return "invalid_candidate_kind"
        for text in (candidate.target_ref, candidate.summary, candidate.rationale):
            if not text.strip() or len(text) > MAX_TEXT_CHARS:
                return "invalid_candidate_text"
        if any(ref not in valid_evidence for ref in candidate.evidence_refs):
            return "untrusted_evidence_reference"
        if not candidate.evidence_refs:
            return "candidate_missing_evidence"
        if candidate.kind == "return_fusion":
            if candidate.target_ref != f"thought-bus://{value.run_id}/fusion":
                return "fusion_target_mismatch"
            if set(candidate.evidence_refs) != valid_evidence:
                return "fusion_evidence_incomplete"
        if candidate.kind == "artifact_proposal":
            if not candidate.target_ref.startswith(_ARTIFACT_TARGET_PREFIXES):
                return "artifact_target_not_allowlisted"
            if ".." in candidate.target_ref:
                return "artifact_target_traversal"
        if candidate.kind == "registered_action" and not candidate.target_ref.startswith("registry://"):
            return "registered_action_target_not_allowlisted"
        try:
            encoded = _canonical_json(candidate.params).encode()
        except (TypeError, ValueError):
            return "candidate_params_not_json"
        if len(encoded) > MAX_PARAMS_BYTES:
            return "candidate_params_budget_exceeded"

    digest = value.evidence_digest
    if not isinstance(digest, EvidenceDigest):
        return "invalid_evidence_digest"
    if len(digest.packet_hash) != 64 or any(char not in "0123456789abcdef" for char in digest.packet_hash):
        return "invalid_packet_hash"
    if value.packet_hash and (
        len(value.packet_hash) != 64
        or any(char not in "0123456789abcdef" for char in value.packet_hash)
    ):
        return "invalid_packet_hash"
    if value.packet_hash and value.packet_hash != digest.packet_hash:
        return "evidence_digest_packet_hash_mismatch"
    if len(digest.entries) > MAX_DIGEST_ENTRIES:
        return "evidence_digest_budget_exceeded"
    digest_receipts: set[str] = set()
    valid_candidate_ids = set(candidate_ids)
    for entry in digest.entries:
        if not isinstance(entry, EvidenceDigestEntry):
            return "invalid_evidence_digest_entry"
        if entry.receipt_id not in valid_evidence or entry.receipt_id in digest_receipts:
            return "invalid_digest_receipt_reference"
        digest_receipts.add(entry.receipt_id)
        if entry.packet_hash != digest.packet_hash:
            return "digest_packet_hash_mismatch"
        if not entry.agent_id.strip() or not entry.role.strip():
            return "invalid_digest_agent_identity"
        if any(candidate not in valid_candidate_ids for candidate in entry.candidate_ids):
            return "invalid_digest_candidate_reference"
        for items in (entry.claims, entry.evidence_refs, entry.risks, entry.blockers):
            if len(items) > MAX_DIGEST_ITEMS:
                return "evidence_digest_item_budget_exceeded"
            if any(not item.strip() or len(item) > MAX_DIGEST_TEXT_CHARS for item in items):
                return "invalid_evidence_digest_text"
    return None


class DeterministicMeditationPolicy:
    """Select one grounded commitment only when the final state converged.

    A registered external action is accepted only through an injected trusted
    validator, and even then the result is still a proposal requiring approval.
    The policy never stages or executes it.
    """

    def __init__(
        self,
        registered_action_validator: Callable[[IntentCandidate], bool] | None = None,
    ) -> None:
        self.registered_action_validator = registered_action_validator

    def resolve(self, value: MeditationInput) -> MeditationResolution:
        value = normalize_meditation_input(value)
        error = _validation_error(value)
        if error:
            return _stop(value, status="blocked", reason=error)
        if value.fusion_status == "blocked" or value.blockers:
            return _stop(value, status="blocked", reason="fusion_blocked")
        if value.fusion_status != "ready":
            return _stop(value, status="abstained", reason="fusion_not_ready")

        actions = [c for c in value.candidates if c.kind == "registered_action"]
        artifacts = [c for c in value.candidates if c.kind == "artifact_proposal"]
        fusions = [c for c in value.candidates if c.kind == "return_fusion"]

        if len(actions) > 1 or len(artifacts) > 1 or len(fusions) > 1:
            return _stop(value, status="abstained", reason="ambiguous_equal_priority_candidates")
        if actions and artifacts:
            return _stop(value, status="abstained", reason="ambiguous_action_and_artifact")

        selected: IntentCandidate | None = None
        if actions:
            action = actions[0]
            if self.registered_action_validator is None or not self.registered_action_validator(action):
                return _stop(value, status="blocked", reason="unvalidated_registered_action")
            selected = replace(action, approval_required=True)
        elif artifacts:
            selected = artifacts[0]
        elif fusions:
            selected = fusions[0]

        if selected is None:
            return _stop(value, status="abstained", reason="no_grounded_candidate")

        intent = commit_intent_for_candidate(value, selected)
        return MeditationResolution(
            receipt=_receipt(
                value,
                status="proposed",
                reason="one_grounded_intent_survived",
                selected_intent_id=intent.id,
                selected_candidate_id=selected.id,
            ),
            commit_intent=intent,
        )


def commit_intent_for_candidate(
    value: MeditationInput,
    candidate: IntentCandidate,
    *,
    confidence: float | None = None,
) -> CommitIntent:
    """Create the exact proposed intent for one already-grounded candidate."""

    value = normalize_meditation_input(value)
    selected = replace(
        candidate,
        approval_required=True if candidate.kind == "registered_action" else candidate.approval_required,
    )
    params = json.loads(_canonical_json(selected.params))
    return CommitIntent(
        id=_intent_id(value, selected),
        run_id=value.run_id,
        workspace_id=value.workspace_id,
        kind=selected.kind,
        target_ref=selected.target_ref,
        summary=selected.summary,
        rationale=selected.rationale,
        evidence_refs=tuple(selected.evidence_refs),
        params=params,
        confidence=round(
            float(value.fusion_confidence if confidence is None else confidence),
            3,
        ),
        approval_required=bool(selected.approval_required),
    )


def validate_meditation_resolution(
    value: MeditationInput,
    resolution: MeditationResolution,
    *,
    allow_approval_intent: bool = False,
) -> str | None:
    """Validate an injected policy result before it crosses the kernel boundary.

    Policies are swappable and therefore untrusted.  They may select one of the
    supplied candidates or abstain; they may not change tenant/run identity,
    invent evidence, rewrite candidate payloads, or smuggle an action through a
    receipt.  Approval-required intents remain disabled in the first bridge.
    """

    value = normalize_meditation_input(value)
    input_error = _validation_error(value)
    if input_error:
        return f"invalid_resolution_input:{input_error}"
    if not isinstance(resolution, MeditationResolution):
        return "invalid_resolution_type"
    receipt = resolution.receipt
    intent = resolution.commit_intent
    if not isinstance(receipt, MeditationReceipt):
        return "invalid_resolution_receipt"
    if receipt.status not in {"proposed", "abstained", "blocked"}:
        return "invalid_resolution_status"
    if tuple(receipt.input_receipt_ids) != tuple(value.input_receipt_ids):
        return "resolution_receipt_scope_mismatch"
    if receipt.candidate_count != len(value.candidates):
        return "resolution_candidate_count_mismatch"
    if not receipt.provider.strip() or not receipt.auth_lane.strip() or not receipt.requested_model.strip():
        return "invalid_resolution_provenance"
    if receipt.resolved_model is not None and not receipt.resolved_model.strip():
        return "invalid_resolution_resolved_model"
    if not isinstance(receipt.fallback_used, bool):
        return "invalid_resolution_fallback_flag"
    if receipt.latency_ms < 0 or receipt.model_call_count not in {0, 1}:
        return "invalid_resolution_call_metadata"
    if receipt.external_actions != "none" or receipt.action_executed is not False:
        return "resolution_claimed_execution"
    if receipt.durable_write is not False:
        return "resolution_claimed_durable_write"
    if not receipt.policy_id.strip() or not receipt.reason.strip():
        return "invalid_resolution_receipt_text"

    if intent is None:
        if (
            receipt.status == "proposed"
            or receipt.selected_intent_id is not None
            or receipt.selected_candidate_id is not None
        ):
            return "resolution_missing_selected_intent"
        if (value.fusion_status == "blocked" or value.blockers) and receipt.status != "blocked":
            return "blocked_fusion_receipt_mismatch"
        if value.fusion_status == "needs_revision" and receipt.status != "abstained":
            return "revision_fusion_receipt_mismatch"
        if receipt.input_hash and receipt.input_hash != meditation_input_hash(value):
            return "resolution_input_hash_mismatch"
        return None

    if not isinstance(intent, CommitIntent):
        return "invalid_commit_intent_type"
    if receipt.status != "proposed":
        return "resolution_intent_without_proposed_receipt"
    if value.fusion_status != "ready" or value.blockers:
        return "resolution_intent_from_unready_fusion"
    if receipt.selected_intent_id != intent.id:
        return "resolution_selected_intent_mismatch"
    if intent.run_id != value.run_id or intent.workspace_id != value.workspace_id:
        return "resolution_tenant_or_run_mismatch"
    if intent.state != "proposed":
        return "resolution_intent_not_proposed"
    if intent.approval_required and not allow_approval_intent:
        return "approval_intent_not_supported_at_bridge"
    if intent.kind == "registered_action" and not intent.approval_required:
        return "registered_action_missing_approval"
    try:
        intent_confidence = float(intent.confidence)
    except (TypeError, ValueError):
        return "invalid_intent_confidence"
    if not 0.0 <= intent_confidence <= 1.0:
        return "invalid_intent_confidence"

    try:
        params_json = _canonical_json(intent.params)
    except (TypeError, ValueError):
        return "intent_params_not_json"
    if len(params_json.encode()) > MAX_PARAMS_BYTES:
        return "intent_params_budget_exceeded"

    matching = []
    for candidate in value.candidates:
        try:
            same_params = _canonical_json(candidate.params) == params_json
        except (TypeError, ValueError):
            continue
        if (
            candidate.kind == intent.kind
            and candidate.target_ref == intent.target_ref
            and candidate.summary == intent.summary
            and candidate.rationale == intent.rationale
            and tuple(candidate.evidence_refs) == tuple(intent.evidence_refs)
            and same_params
        ):
            matching.append(candidate)
    if len(matching) != 1:
        return "intent_did_not_select_exactly_one_candidate"
    if receipt.selected_candidate_id != matching[0].id:
        return "resolution_selected_candidate_mismatch"
    expected_approval = True if matching[0].kind == "registered_action" else matching[0].approval_required
    if intent.approval_required is not expected_approval:
        return "intent_approval_mismatch"
    if any(ref not in value.input_receipt_ids for ref in intent.evidence_refs):
        return "intent_invented_evidence"
    if intent.id != _intent_id(value, matching[0]):
        return "intent_id_mismatch"
    if receipt.input_hash and receipt.input_hash != meditation_input_hash(value):
        return "resolution_input_hash_mismatch"
    return None


def build_meditation_input(
    *,
    packet: Any,
    results: Iterable[Any],
    receipts: Iterable[Any],
    decision: Any,
) -> MeditationInput:
    """Build the trusted meditation input while full agent imprints exist.

    Sandboxed outputs and memory candidates are intentionally excluded.  The
    fallback candidate merely proposes returning the already-fused decision.
    """

    result_list = list(results)
    receipt_list = list(receipts)
    receipt_ids = tuple(str(receipt.id) for receipt in receipt_list)
    shared_packet_hash = packet_hash(packet)
    fusion_target = f"thought-bus://{packet.run_id}/fusion"
    candidates: list[IntentCandidate] = [
        IntentCandidate(
            id=candidate_id(
                run_id=str(packet.run_id),
                kind="return_fusion",
                target_ref=fusion_target,
                evidence_refs=receipt_ids,
            ),
            kind="return_fusion",
            target_ref=fusion_target,
            summary=str(decision.summary),
            rationale=str(decision.reason),
            evidence_refs=receipt_ids,
            params={
                "fusionStatus": str(decision.status),
                "fusionConfidence": float(decision.confidence),
            },
        )
    ]

    for result, receipt in zip(result_list, receipt_list):
        for artifact in getattr(result, "proposed_artifacts", ()):
            if not isinstance(artifact, dict):
                continue
            artifact_type = str(artifact.get("type") or "")
            target = str(artifact.get("path") or artifact.get("targetRef") or "").strip()
            if artifact_type not in {"artifact_proposal", "draft_artifact"}:
                continue
            if artifact.get("mergeAllowed") is not False or artifact.get("durableWrite") is not False:
                continue
            if not target or target.startswith("sandbox://") or target.startswith("sandbox/"):
                continue
            if not target.startswith(_ARTIFACT_TARGET_PREFIXES) or ".." in target:
                continue
            candidates.append(IntentCandidate(
                id=candidate_id(
                    run_id=str(packet.run_id),
                    kind="artifact_proposal",
                    target_ref=target,
                    evidence_refs=(str(receipt.id),),
                ),
                kind="artifact_proposal",
                target_ref=target,
                summary=str(artifact.get("description") or "Review the proposed artifact."),
                rationale=f"Proposed by Thought Bus role {getattr(result, 'role', 'unknown')}.",
                evidence_refs=(str(receipt.id),),
                params={"artifactType": artifact_type},
            ))

    candidate_ids_by_receipt: dict[str, list[str]] = {receipt_id: [] for receipt_id in receipt_ids}
    for candidate in candidates:
        for receipt_id in candidate.evidence_refs:
            candidate_ids_by_receipt.setdefault(receipt_id, []).append(candidate.id)

    digest_entries: list[EvidenceDigestEntry] = []
    for result, receipt in zip(result_list, receipt_list):
        if len(digest_entries) >= MAX_DIGEST_ENTRIES:
            break
        # Packet provenance is kernel-owned. Never trust an agent result to
        # replace the shared ThoughtPacket hash.
        result_packet_hash = shared_packet_hash
        digest_entries.append(EvidenceDigestEntry(
            receipt_id=str(receipt.id),
            agent_id=str(getattr(result, "agent_id", "unknown"))[:128],
            role=str(getattr(result, "role", "unknown"))[:128],
            packet_hash=result_packet_hash,
            claims=_digest_items(getattr(result, "claims", ())),
            evidence_refs=_digest_items(getattr(result, "evidence", ())),
            risks=_digest_items(getattr(result, "risks", ())),
            blockers=_digest_items(getattr(result, "blockers", ())),
            candidate_ids=tuple(candidate_ids_by_receipt.get(str(receipt.id), ())),
        ))

    return MeditationInput(
        run_id=str(packet.run_id),
        workspace_id=str(packet.workspace_id),
        fusion_status=decision.status,
        fusion_confidence=float(decision.confidence),
        fusion_reason=str(decision.reason),
        blockers=tuple(str(item) for item in decision.blockers),
        conflicts=tuple(str(item) for item in decision.conflicts),
        input_receipt_ids=receipt_ids,
        candidates=tuple(candidates),
        evidence_digest=EvidenceDigest(
            packet_hash=shared_packet_hash,
            entries=tuple(digest_entries),
        ),
        packet_hash=shared_packet_hash,
    )
