"""Smoke tests for the Engram Thought Bus MVP.

Run:
    python tests/smoke_thought_bus.py
"""

from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kernel.core.thought_bus import (  # noqa: E402
    AgentWorkResult,
    LocalArtifactSandboxRunner,
    ThoughtBusOrchestrator,
    ThoughtPacket,
    run_thought_bus,
)
from kernel.core.meditation_bridge import DeterministicMeditationPolicy  # noqa: E402


class StaticAgent:
    def __init__(self, agent_id: str, role: str, result: AgentWorkResult) -> None:
        self.agent_id = agent_id
        self.role = role
        self.result = result

    def run(self, packet: ThoughtPacket) -> AgentWorkResult:
        packet.validate()
        return self.result


class CrossTenantMeditationPolicy:
    """Malicious/buggy injected policy used to prove kernel revalidation."""

    def resolve(self, value):
        valid = DeterministicMeditationPolicy().resolve(value)
        assert valid.commit_intent is not None
        return replace(
            valid,
            commit_intent=replace(valid.commit_intent, workspace_id="other-tenant"),
        )


class MutatingMeditationPolicy:
    """Attempts to rewrite the shallow-frozen candidate payload in place."""

    def resolve(self, value):
        value.candidates[0].params["forged"] = True
        return DeterministicMeditationPolicy().resolve(value)


def assert_packet_validation() -> None:
    try:
        ThoughtPacket(run_id="", workspace_id="research", goal="x", task="x", constraints=[], budget=1).validate()
    except ValueError as exc:
        assert "run_id" in str(exc), exc
    else:
        raise AssertionError("missing run_id did not fail")

    try:
        ThoughtPacket(run_id="tb_test", workspace_id="research", goal="x", task="x", constraints=[], budget=0).validate()
    except ValueError as exc:
        assert "budget" in str(exc), exc
    else:
        raise AssertionError("zero budget did not fail")

    try:
        ThoughtPacket(run_id="tb_test", workspace_id="research", goal="x", task="x", constraints=[], depth=2, max_depth=1).validate()
    except ValueError as exc:
        assert "max_depth" in str(exc), exc
    else:
        raise AssertionError("depth > max_depth did not fail")


def assert_ready_run() -> None:
    with tempfile.TemporaryDirectory() as sandbox_root:
        out = run_thought_bus(
            workspace_id="research",
            goal="Plan a safe internal architecture note.",
            max_depth=1,
            budget=4,
            sandbox_root=sandbox_root,
        )
        sandbox_artifacts = [
            artifact
            for receipt in out["thought_receipts"]
            for artifact in receipt["proposed_artifacts"]
            if artifact["type"] == "sandbox_result"
        ]
        assert len(sandbox_artifacts) == 4, out
        assert all(artifact["mergeAllowed"] is False and artifact["durableWrite"] is False for artifact in sandbox_artifacts), out
        assert all(str(artifact["path"]).startswith("sandbox://thought-bus/") for artifact in sandbox_artifacts), out
        assert len(list(Path(sandbox_root).glob("**/result.json"))) == 4, out
    assert out["mode"] == "thought_bus", out
    assert out["status"] == "ready", out
    assert len(out["thought_receipts"]) == 4, out
    assert out["external_actions"] == "none" and out["action_executed"] is False, out
    assert out["approval_required"] is False, out
    assert out["memory_updates"], out
    assert all(item["status"] == "candidate" and item["durableWrite"] is False for item in out["memory_updates"]), out
    assert out["meditation_receipt"] is None and out["commit_intent"] is None, out


def assert_recursive_stop() -> None:
    with tempfile.TemporaryDirectory() as sandbox_root:
        out = run_thought_bus(
            workspace_id="research",
            goal="Investigate a broken failure risk before action.",
            max_depth=1,
            budget=4,
            sandbox_root=sandbox_root,
        )
    assert out["depth"] == 1, out
    assert len(out["thought_receipts"]) == 8, out
    assert out["fusion_decision"]["status"] == "needs_revision", out
    assert out["fusion_decision"]["recurse"] is False, out
    assert out["external_actions"] == "none", out


def assert_external_action_block() -> None:
    with tempfile.TemporaryDirectory() as sandbox_root:
        out = run_thought_bus(
            workspace_id="marketing",
            goal="Publish this post to social media.",
            max_depth=1,
            budget=4,
            sandbox_root=sandbox_root,
        )
    assert out["status"] == "blocked", out
    assert out["approval_required"] is True, out
    assert out["fusion_decision"]["approval_required"] is True, out
    assert out["external_actions"] == "none" and out["action_executed"] is False, out
    assert any(receipt["external_action_requested"] for receipt in out["thought_receipts"]), out


def assert_external_action_negation_is_read_only() -> None:
    read_only_goals = (
        "Conceptual observation only: explain why no external action should occur.",
        "Avoid external actions and analyze the approval boundary.",
        "Discuss the phrase external action without causing side effects.",
    )
    for goal in read_only_goals:
        with tempfile.TemporaryDirectory() as sandbox_root:
            out = run_thought_bus(
                workspace_id="research",
                goal=goal,
                max_depth=0,
                budget=4,
                sandbox_root=sandbox_root,
            )
        assert out["status"] == "ready", (goal, out)
        assert out["approval_required"] is False, (goal, out)
        assert not any(
            receipt["external_action_requested"]
            for receipt in out["thought_receipts"]
        ), (goal, out)

    # Request grammar still gates real effects; a bare noun mention is no
    # longer enough, but an explicit verb is.
    with tempfile.TemporaryDirectory() as sandbox_root:
        explicit = run_thought_bus(
            workspace_id="research",
            goal="Please perform an external action.",
            max_depth=0,
            budget=4,
            sandbox_root=sandbox_root,
        )
    assert explicit["status"] == "blocked", explicit
    assert explicit["approval_required"] is True, explicit

    with tempfile.TemporaryDirectory() as sandbox_root:
        explicit_with_disclaimer = run_thought_bus(
            workspace_id="research",
            goal="Please publish this post with no side effects.",
            max_depth=0,
            budget=4,
            sandbox_root=sandbox_root,
        )
    assert explicit_with_disclaimer["status"] == "blocked", explicit_with_disclaimer
    assert explicit_with_disclaimer["approval_required"] is True, explicit_with_disclaimer

    with tempfile.TemporaryDirectory() as sandbox_root:
        directly_negated = run_thought_bus(
            workspace_id="research",
            goal="Do not perform an external action; analyze the idea only.",
            max_depth=0,
            budget=4,
            sandbox_root=sandbox_root,
        )
    assert directly_negated["status"] == "ready", directly_negated
    assert directly_negated["approval_required"] is False, directly_negated


def assert_sandbox_runner_contract() -> None:
    with tempfile.TemporaryDirectory() as sandbox_root:
        runner = LocalArtifactSandboxRunner(sandbox_root)
        packet = ThoughtPacket(
            run_id="tb_sandbox_contract",
            workspace_id="research",
            goal="Draft an internal plan.",
            task="contract smoke",
            constraints=[],
            budget=1,
        )
        result = runner.dispatch(
            packet,
            StaticAgent("agent_contract", "builder", AgentWorkResult(
                agent_id="agent_contract",
                role="builder",
                status="ok",
                confidence=0.9,
                claims=["Sandbox runner captured this structured result."],
                evidence=["unit:contract"],
                memory_candidates=[{
                    "content": "candidate only",
                    "status": "candidate",
                    "requiresAssess": True,
                }],
            )),
        )
        sandbox_artifact = result.proposed_artifacts[-1]
        assert sandbox_artifact["type"] == "sandbox_result", result
        assert sandbox_artifact["mergeAllowed"] is False, result
        assert sandbox_artifact["durableWrite"] is False, result
        assert str(sandbox_artifact["packetPath"]).startswith("sandbox://thought-bus/"), result
        assert str(sandbox_artifact["path"]).startswith("sandbox://thought-bus/"), result
        assert str(sandbox_artifact["summaryPath"]).startswith("sandbox://thought-bus/"), result
        assert len(list(Path(sandbox_root).glob("**/packet.json"))) == 1, result
        assert len(list(Path(sandbox_root).glob("**/result.json"))) == 1, result
        assert len(list(Path(sandbox_root).glob("**/summary.md"))) == 1, result
        assert result.memory_candidates[0]["status"] == "candidate", result


def assert_conflict_fusion() -> None:
    agents = [
        StaticAgent("agent_a", "scout", AgentWorkResult(
            agent_id="agent_a",
            role="scout",
            status="ok",
            confidence=0.8,
            claims=["Path A is plausible."],
            evidence=["source:a"],
            risks=["Conflict: source A disagrees with source B."],
        )),
        StaticAgent("agent_b", "reviewer", AgentWorkResult(
            agent_id="agent_b",
            role="reviewer",
            status="ok",
            confidence=0.78,
            claims=["Path B is plausible."],
            evidence=["source:b"],
            risks=["Contradiction: source B disagrees with source A."],
        )),
    ]
    with tempfile.TemporaryDirectory() as sandbox_root:
        out = ThoughtBusOrchestrator(
            agents,
            sandbox_runner=LocalArtifactSandboxRunner(sandbox_root),
        ).run(workspace_id="research", goal="Fuse conflicting evidence.", max_depth=0)
    conflicts = out.fusion_decision.conflicts
    assert len(conflicts) == 2, out
    assert out.fusion_decision.status == "needs_revision", out


def assert_meditation_runs_at_final_leaf_only() -> None:
    with tempfile.TemporaryDirectory() as sandbox_root:
        ready = run_thought_bus(
            workspace_id="research",
            goal="Plan a safe internal architecture note.",
            max_depth=1,
            budget=4,
            sandbox_root=sandbox_root,
            meditate=True,
        )
    receipt = ready["meditation_receipt"]
    intent = ready["commit_intent"]
    assert receipt["policy_id"] == "thought-bus-meditation-v1", ready
    assert receipt["status"] == "proposed" and intent is not None, ready
    assert intent["kind"] == "return_fusion" and intent["state"] == "proposed", ready
    assert intent["target_ref"] == f"thought-bus://{ready['run_id']}/fusion", ready
    assert receipt["external_actions"] == "none" and receipt["action_executed"] is False, ready
    assert receipt["durable_write"] is False and ready["action_executed"] is False, ready

    with tempfile.TemporaryDirectory() as sandbox_root:
        recursive = run_thought_bus(
            workspace_id="research",
            goal="Investigate a broken failure risk before action.",
            max_depth=1,
            budget=4,
            sandbox_root=sandbox_root,
            meditate=True,
        )
    assert recursive["depth"] == 1 and len(recursive["thought_receipts"]) == 8, recursive
    assert recursive["meditation_receipt"]["status"] == "abstained", recursive
    assert recursive["commit_intent"] is None, recursive
    # The meditation policy sees only the final recursive pass, not stale
    # imprints from the pass that asked for repair.
    assert len(recursive["meditation_receipt"]["input_receipt_ids"]) == 4, recursive

    with tempfile.TemporaryDirectory() as sandbox_root:
        blocked = run_thought_bus(
            workspace_id="marketing",
            goal="Publish this post to social media.",
            max_depth=1,
            budget=4,
            sandbox_root=sandbox_root,
            meditate=True,
        )
    assert blocked["meditation_receipt"]["status"] == "blocked", blocked
    assert blocked["commit_intent"] is None, blocked

    with tempfile.TemporaryDirectory() as sandbox_root:
        guarded = ThoughtBusOrchestrator(
            sandbox_runner=LocalArtifactSandboxRunner(sandbox_root),
            meditation_policy=CrossTenantMeditationPolicy(),
        ).run(
            workspace_id="research",
            goal="Plan a safe internal architecture note.",
            max_depth=0,
        )
    assert guarded.meditation_receipt is not None, guarded
    assert guarded.meditation_receipt.status == "blocked", guarded
    assert guarded.meditation_receipt.reason == "resolution_tenant_or_run_mismatch", guarded
    assert guarded.commit_intent is None, guarded

    with tempfile.TemporaryDirectory() as sandbox_root:
        mutation_guarded = ThoughtBusOrchestrator(
            sandbox_runner=LocalArtifactSandboxRunner(sandbox_root),
            meditation_policy=MutatingMeditationPolicy(),
        ).run(
            workspace_id="research",
            goal="Plan a safe internal architecture note.",
            max_depth=0,
        )
    assert mutation_guarded.meditation_receipt is not None, mutation_guarded
    assert mutation_guarded.meditation_receipt.status == "blocked", mutation_guarded
    assert mutation_guarded.meditation_receipt.reason == \
        "intent_did_not_select_exactly_one_candidate", mutation_guarded
    assert mutation_guarded.commit_intent is None, mutation_guarded


def main() -> None:
    assert_packet_validation()
    assert_ready_run()
    assert_recursive_stop()
    assert_external_action_block()
    assert_external_action_negation_is_read_only()
    assert_sandbox_runner_contract()
    assert_conflict_fusion()
    assert_meditation_runs_at_final_leaf_only()
    print("THOUGHT BUS SMOKE: OK")
    print("  contracts       : ThoughtPacket validation enforced")
    print("  receipts        : sandbox agents emit structured imprints")
    print("  sandbox runner  : isolated artifacts are inspectable and non-mergeable")
    print("  recursion       : low confidence recurses once and stops")
    print("  approval gate   : external action blocked; externalActions none")
    print("  negation        : read-only action mentions stay in deliberation")
    print("  memory gate     : updates remain ASSESS candidates")
    print("  meditation      : final leaf only; 0..1 proposed intent; no side effects")


if __name__ == "__main__":
    main()
