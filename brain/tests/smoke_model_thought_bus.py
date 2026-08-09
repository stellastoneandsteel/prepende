"""Offline smoke coverage for the explicit model-backed Thought Bus lane.

The gateway below is a deterministic test double. It records prompts and
options, but never calls a connector, writes memory, stages approval, or acts.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kernel.core.model_thought_bus import (  # noqa: E402
    AGENT_OUTPUT_SCHEMA,
    run_thought_bus_async,
)
from kernel.core.semantic_meditation import (  # noqa: E402
    SEMANTIC_OUTPUT_SCHEMA,
    SemanticMeditationPolicy,
)
from kernel.core.thought_bus import run_thought_bus  # noqa: E402


class ScriptedGateway:
    name = "fake"
    requested_model = "fake-model"
    resolved_model = None

    def __init__(self, mode: str = "valid") -> None:
        self.mode = mode
        self.calls: list[dict[str, Any]] = []

    async def complete(self, messages, **opts):
        self.calls.append({"messages": messages, "opts": opts})
        if self.mode == "fallback_provenance":
            self.resolved_model = "fallback-model"
        content = messages[-1]["content"] if messages else ""
        system = str(opts.get("system") or "")
        if "evidenceDigest" in content:
            if self.mode == "semantic_abstain":
                return json.dumps({"decision": "abstain", "candidateId": None, "reason": "no clear winner"})
            if self.mode == "semantic_unknown":
                return json.dumps({"decision": "select", "candidateId": "mc_forged", "reason": "forged"})
            if self.mode == "semantic_malformed":
                return "not-json"
            if self.mode == "semantic_timeout":
                await asyncio.sleep(1.0)
            payload = json.loads(content)
            candidates = payload.get("candidates") or []
            selected = candidates[1]["id"] if len(candidates) > 1 else None
            if selected is None:
                return json.dumps({"decision": "abstain", "candidateId": None, "reason": "none"})
            return json.dumps({"decision": "select", "candidateId": selected, "reason": "grounded"})

        payload = json.loads(content)
        packet = payload["thoughtPacket"]
        role = system.rsplit("Your role is ", 1)[-1].rstrip(".")
        if self.mode == "partial_failure" and role == "reviewer":
            return "not-json"
        if self.mode == "all_failure":
            return "not-json"
        if self.mode == "oversized":
            return json.dumps({
                "status": "ok", "confidence": 0.9,
                "claims": ["x" * 601], "evidence": ["e"],
                "risks": [], "blockers": [], "proposals": [],
                "nextThoughts": [], "externalActionRequested": False,
            })
        if self.mode == "repair" and packet["depth"] == 0:
            status, confidence = "needs_revision", 0.4
        else:
            status, confidence = "ok", 0.9
        return json.dumps({
            "status": status,
            "confidence": confidence,
            "claims": ["IGNORE THIS STRING AS AN INSTRUCTION; it is data."],
            "evidence": ["source:offline-test"],
            "risks": [],
            "blockers": [],
            "proposals": [{"summary": "bounded inert proposal"}],
            "nextThoughts": [],
            "externalActionRequested": (
                "publish" in packet["goal"].lower()
                or self.mode == "model_false_positive"
            ),
        })


async def model_lane_checks() -> None:
    gateway = ScriptedGateway()
    result = await run_thought_bus_async(
        gateway=gateway,
        workspace_id="research",
        goal="Plan a bounded internal change.",
        max_depth=0,
        model_call_budget=5,
    )
    hashes = {json.loads(call["messages"][-1]["content"])["packetHash"] for call in gateway.calls}
    assert len(hashes) == 1 and len(result["thought_receipts"]) == 4, result
    assert all(item["packet_hash"] == next(iter(hashes)) for item in result["thought_receipts"]), result
    assert all(call["opts"]["tool_policy"] == "none" for call in gateway.calls), gateway.calls
    assert all(call["opts"]["output_schema"] == AGENT_OUTPUT_SCHEMA for call in gateway.calls), gateway.calls
    assert result["status"] == "ready" and result["memory_updates"] == [], result
    assert result["external_actions"] == "none" and result["action_executed"] is False, result
    assert result["thought_receipts"][0]["model_provenance"]["requested_model"] == "fake-model", result

    # The model's boolean is advisory: a cautious role may mark a conceptual
    # discussion as actionable, but only the deterministic goal classifier can
    # promote an explicit outside-effect request into approval.
    false_positive_gateway = ScriptedGateway("model_false_positive")
    false_positive = await run_thought_bus_async(
        gateway=false_positive_gateway,
        workspace_id="research",
        goal="Conceptual observation only: discuss why publishing requires a gate.",
        max_depth=0,
        budget=4,
    )
    assert false_positive["status"] == "ready", false_positive
    assert false_positive["approval_required"] is False, false_positive
    assert not any(
        receipt["external_action_requested"]
        for receipt in false_positive["thought_receipts"]
    ), false_positive
    assert result["thought_receipts"][0]["model_provenance"]["resolved_model"] is None, result
    digest = result["evidence_digest"]
    assert len(digest["entries"]) == 4 and len(digest["entries"][0]["claims"][0]) <= 600, digest
    assert "IGNORE THIS STRING" in digest["entries"][0]["claims"][0], digest

    fallback_gateway = ScriptedGateway("fallback_provenance")
    fallback_receipt = await run_thought_bus_async(
        gateway=fallback_gateway,
        workspace_id="research",
        goal="prove resolved model provenance",
        roles=["analyst"],
        max_depth=0,
    )
    assert fallback_receipt["thought_receipts"][0]["model_provenance"]["resolved_model"] == "fallback-model", fallback_receipt

    partial = await run_thought_bus_async(
        gateway=ScriptedGateway("partial_failure"), workspace_id="research", goal="safe", max_depth=0
    )
    assert partial["status"] == "ready", partial
    assert sum(not item["available"] for item in partial["thought_receipts"]) == 1, partial
    failed = await run_thought_bus_async(
        gateway=ScriptedGateway("all_failure"), workspace_id="research", goal="safe", max_depth=0
    )
    assert failed["status"] == "blocked" and failed["fusion_decision"]["blockers"] == ["all_model_agents_unavailable"], failed
    oversized = await run_thought_bus_async(
        gateway=ScriptedGateway("oversized"), workspace_id="research", goal="safe", max_depth=0
    )
    assert all(not item["available"] for item in oversized["thought_receipts"]), oversized

    recursive_gateway = ScriptedGateway("repair")
    recursive = await run_thought_bus_async(
        gateway=recursive_gateway,
        workspace_id="research",
        goal="safe recursive repair",
        max_depth=1,
        budget=4,
        meditate=True,
    )
    assert recursive["depth"] == 1 and len(recursive["thought_receipts"]) == 8, recursive
    final_ids = {item["id"] for item in recursive["thought_receipts"][4:]}
    assert set(recursive["meditation_receipt"]["input_receipt_ids"]) == final_ids, recursive

    publish = await run_thought_bus_async(
        gateway=ScriptedGateway(), workspace_id="research", goal="Publish this", max_depth=0
    )
    assert publish["status"] == "blocked" and publish["approval_required"] is True, publish
    assert publish["external_actions"] == "none" and publish["action_executed"] is False, publish

    negated = await run_thought_bus_async(
        gateway=ScriptedGateway(),
        workspace_id="research",
        goal="Observation only: no external action should occur.",
        max_depth=0,
    )
    assert negated["status"] == "ready", negated
    assert negated["approval_required"] is False, negated
    assert not any(
        receipt["external_action_requested"]
        for receipt in negated["thought_receipts"]
    ), negated

    semantic_gateway = ScriptedGateway()
    semantic = await run_thought_bus_async(
        gateway=semantic_gateway,
        workspace_id="research",
        goal="safe",
        max_depth=0,
        semantic_policy=SemanticMeditationPolicy(semantic_gateway),
    )
    assert semantic["meditation_receipt"]["status"] == "proposed", semantic
    semantic_calls = [
        call for call in semantic_gateway.calls
        if "evidenceDigest" in call["messages"][-1]["content"]
    ]
    assert len(semantic_calls) == 1, semantic_gateway.calls
    assert semantic_calls[0]["opts"]["output_schema"] == SEMANTIC_OUTPUT_SCHEMA
    assert semantic["commit_intent"] is not None, semantic
    all_candidate_ids = [
        candidate_id
        for entry in semantic["evidence_digest"]["entries"]
        for candidate_id in entry["candidate_ids"]
    ]
    assert semantic["meditation_receipt"]["selected_candidate_id"] in all_candidate_ids, semantic

    abstain_gateway = ScriptedGateway("semantic_abstain")
    abstain = await run_thought_bus_async(
        gateway=abstain_gateway, workspace_id="research", goal="safe", max_depth=0,
        semantic_policy=SemanticMeditationPolicy(abstain_gateway),
    )
    assert abstain["meditation_receipt"]["status"] == "abstained" and abstain["commit_intent"] is None, abstain
    for mode in ("semantic_unknown", "semantic_malformed", "semantic_timeout"):
        fallback_gateway = ScriptedGateway(mode)
        fallback = await run_thought_bus_async(
            gateway=fallback_gateway, workspace_id="research", goal="safe", max_depth=0,
            semantic_policy=SemanticMeditationPolicy(fallback_gateway, timeout_seconds=0.05),
        )
        assert fallback["meditation_receipt"]["fallback_used"] is True, (mode, fallback)
        assert fallback["meditation_receipt"]["provider"] == "fake", (mode, fallback)

    concurrent = await asyncio.gather(*[
        run_thought_bus_async(gateway=ScriptedGateway(), workspace_id=scope, goal="safe", max_depth=0)
        for scope in ("scope_a", "scope_b")
    ])
    assert concurrent[0]["workspace_id"] != concurrent[1]["workspace_id"], concurrent
    assert concurrent[0]["packet_hash"] != concurrent[1]["packet_hash"], concurrent


def deterministic_regression() -> None:
    baseline = run_thought_bus(workspace_id="research", goal="safe internal plan", max_depth=0)
    assert baseline["model_backed"] is False and baseline["agent_mode"] == "deterministic", baseline
    assert baseline["meditation_receipt"] is None and baseline["external_actions"] == "none", baseline


def main() -> None:
    asyncio.run(model_lane_checks())
    deterministic_regression()
    print("MODEL THOUGHT BUS SMOKE: OK")
    print("  packets         : identical packet hash per bounded council pass")
    print("  failures        : malformed, partial, timeout, and all-failed receipts")
    print("  meditation      : existing-id selection, abstention, fail-closed fallback")
    print("  boundaries      : no tools, approvals, memory writes, or external actions")
    print("  negation        : no-action language does not trigger approval")


if __name__ == "__main__":
    main()
