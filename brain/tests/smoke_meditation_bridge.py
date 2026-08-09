"""Smoke: pure Thought Bus meditation resolves to zero or one intent.

Run:
    python3 tests/smoke_meditation_bridge.py
"""

from __future__ import annotations

from dataclasses import replace
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kernel.contracts.meditation import IntentCandidate, MeditationInput  # noqa: E402
from kernel.core.meditation_bridge import (  # noqa: E402
    MAX_CANDIDATES,
    DeterministicMeditationPolicy,
    validate_meditation_resolution,
)


RECEIPTS = ("tr_a", "tr_b")


def _candidate(kind="return_fusion", target="thought-bus://tb_test/fusion", **overrides):
    values = {
        "kind": kind,
        "target_ref": target,
        "summary": "Return the smallest grounded decision.",
        "rationale": "The final Thought Bus pass converged.",
        "evidence_refs": RECEIPTS,
        "params": {"version": 1},
        "approval_required": False,
    }
    values.update(overrides)
    return IntentCandidate(**values)


def _input(*, status="ready", blockers=(), candidates=None):
    return MeditationInput(
        run_id="tb_test",
        workspace_id="research",
        fusion_status=status,
        fusion_confidence=0.84,
        fusion_reason="The final pass converged.",
        blockers=tuple(blockers),
        input_receipt_ids=RECEIPTS,
        candidates=tuple(candidates if candidates is not None else [_candidate()]),
    )


def assert_ready_is_exactly_one_and_idempotent() -> None:
    policy = DeterministicMeditationPolicy()
    first = policy.resolve(_input())
    second = policy.resolve(_input())
    assert first.receipt.status == "proposed", first
    assert first.commit_intent is not None, first
    assert first.commit_intent.id == second.commit_intent.id, (first, second)
    assert first.receipt.selected_intent_id == first.commit_intent.id, first
    assert first.commit_intent.state == "proposed", first
    _assert_no_side_effect_receipt(first)


def assert_revision_and_blocker_abstain() -> None:
    policy = DeterministicMeditationPolicy()
    revision = policy.resolve(_input(status="needs_revision"))
    assert revision.receipt.status == "abstained" and revision.commit_intent is None, revision
    blocked = policy.resolve(_input(status="blocked", blockers=("external_action_requires_approval",)))
    assert blocked.receipt.status == "blocked" and blocked.commit_intent is None, blocked
    _assert_no_side_effect_receipt(revision)
    _assert_no_side_effect_receipt(blocked)


def assert_equal_priority_artifacts_abstain() -> None:
    artifacts = [
        _candidate("artifact_proposal", "artifact://draft-a"),
        _candidate("artifact_proposal", "artifact://draft-b"),
        _candidate(),
    ]
    out = DeterministicMeditationPolicy().resolve(_input(candidates=artifacts))
    assert out.receipt.status == "abstained", out
    assert out.receipt.reason == "ambiguous_equal_priority_candidates", out
    assert out.commit_intent is None, out


def assert_forged_evidence_and_budget_fail_closed() -> None:
    forged = _candidate(evidence_refs=("tr_forged",))
    out = DeterministicMeditationPolicy().resolve(_input(candidates=[forged]))
    assert out.receipt.status == "blocked" and out.commit_intent is None, out
    assert out.receipt.reason == "untrusted_evidence_reference", out

    too_many = [_candidate(target=f"thought-bus://tb_test/fusion/{i}") for i in range(MAX_CANDIDATES + 1)]
    overflow = DeterministicMeditationPolicy().resolve(_input(candidates=too_many))
    assert overflow.receipt.status == "blocked" and overflow.commit_intent is None, overflow
    assert overflow.receipt.reason == "candidate_budget_exceeded", overflow

    not_json = _candidate(params={"value": float("nan")})
    invalid_json = DeterministicMeditationPolicy().resolve(_input(candidates=[not_json]))
    assert invalid_json.receipt.status == "blocked" and invalid_json.commit_intent is None, invalid_json
    assert invalid_json.receipt.reason == "candidate_params_not_json", invalid_json

    unsafe_target = _candidate("artifact_proposal", "/etc/passwd", evidence_refs=("tr_a",))
    unsafe = DeterministicMeditationPolicy().resolve(_input(candidates=[unsafe_target, _candidate()]))
    assert unsafe.receipt.status == "blocked" and unsafe.commit_intent is None, unsafe
    assert unsafe.receipt.reason == "artifact_target_not_allowlisted", unsafe


def assert_registered_action_is_proposal_only() -> None:
    checked: list[str] = []

    def validate(candidate: IntentCandidate) -> bool:
        checked.append(candidate.target_ref)
        return candidate.target_ref == "registry://workflow.research_digest"

    action = _candidate(
        "registered_action",
        "registry://workflow.research_digest",
        evidence_refs=("tr_a",),
        params={"registryEntryId": "workflow.research_digest"},
    )
    out = DeterministicMeditationPolicy(validate).resolve(_input(candidates=[action, _candidate()]))
    assert checked == ["registry://workflow.research_digest"], checked
    assert out.receipt.status == "proposed" and out.commit_intent is not None, out
    assert out.commit_intent.approval_required is True, out
    assert out.commit_intent.state == "proposed", out
    _assert_no_side_effect_receipt(out)


def assert_injected_policy_output_is_revalidated() -> None:
    value = _input()
    valid = DeterministicMeditationPolicy().resolve(value)
    assert validate_meditation_resolution(value, valid) is None, valid
    assert valid.commit_intent is not None, valid

    wrong_tenant = replace(
        valid,
        commit_intent=replace(valid.commit_intent, workspace_id="other-tenant"),
    )
    assert validate_meditation_resolution(value, wrong_tenant) == "resolution_tenant_or_run_mismatch"

    wrong_selected_id = replace(
        valid,
        receipt=replace(valid.receipt, selected_intent_id="ci_forged"),
    )
    assert validate_meditation_resolution(value, wrong_selected_id) == "resolution_selected_intent_mismatch"

    approval_smuggle = replace(
        valid,
        commit_intent=replace(valid.commit_intent, approval_required=True),
    )
    assert validate_meditation_resolution(value, approval_smuggle) == "approval_intent_not_supported_at_bridge"

    unready = replace(value, fusion_status="blocked", blockers=("safety",))
    assert validate_meditation_resolution(unready, valid) == "resolution_intent_from_unready_fusion"

    garbage_status = replace(
        valid,
        receipt=replace(valid.receipt, status="garbage", selected_intent_id=None),  # type: ignore[arg-type]
        commit_intent=None,
    )
    assert validate_meditation_resolution(value, garbage_status) == "invalid_resolution_status"

    oversized_input = replace(value, candidates=tuple(_candidate() for _ in range(MAX_CANDIDATES + 1)))
    assert validate_meditation_resolution(oversized_input, valid) == \
        "invalid_resolution_input:candidate_budget_exceeded"

    approval_candidate = _candidate(
        "artifact_proposal",
        "artifact://review-required",
        evidence_refs=("tr_a",),
        approval_required=True,
    )
    approval_input = _input(candidates=[approval_candidate])
    approval_resolution = DeterministicMeditationPolicy().resolve(approval_input)
    assert approval_resolution.commit_intent is not None
    downgraded = replace(
        approval_resolution,
        commit_intent=replace(approval_resolution.commit_intent, approval_required=False),
    )
    assert validate_meditation_resolution(
        approval_input,
        downgraded,
        allow_approval_intent=True,
    ) == "intent_approval_mismatch"


def _assert_no_side_effect_receipt(value) -> None:
    assert value.receipt.external_actions == "none", value
    assert value.receipt.action_executed is False, value
    assert value.receipt.durable_write is False, value


def main() -> None:
    assert_ready_is_exactly_one_and_idempotent()
    assert_revision_and_blocker_abstain()
    assert_equal_priority_artifacts_abstain()
    assert_forged_evidence_and_budget_fail_closed()
    assert_registered_action_is_proposal_only()
    assert_injected_policy_output_is_revalidated()
    print("MEDITATION BRIDGE SMOKE: OK")
    print("  convergence     : ready -> exactly one deterministic proposed intent")
    print("  restraint       : revision, blockers, ambiguity -> zero intent")
    print("  trust boundary  : forged/oversized inputs fail closed")
    print("  policy boundary : injected results are revalidated before surfacing")
    print("  actions         : registry validation + approval required; never executed")
    print("  persistence     : no durable write")


if __name__ == "__main__":
    main()
