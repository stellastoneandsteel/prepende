"""Smoke: the loop decision, run receipts, verifier repair, and gates.

Proves, with fakes only (no network, no real model):
  1. normal chat does NOT enter the goal loop
  2. a complex goal DOES enter the goal loop
  3. external actions are routed to approval, never executed from chat
  4. hierarchical recursion stops at the hard step cap
  5. a low-confidence verifier triggers exactly ONE repair
  6. under the candidate memory policy the loop proposes but never writes
  7. the run receipt is truthful (mode, tactic, verifier, memory, actions)

Run: python3 tests/smoke_loop_receipts.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from interface.prepende_runtime import _chat_route
from kernel.core.loop import GoalLoop
from kernel.core.strategist import RulesStrategist
from kernel.core.types import Goal
from tactics.hierarchical import HierarchicalTactic
from workspace.local import LocalWorkspace


class FakeGateway:
    """Counts calls; returns a canned plan for plan prompts, else canned text."""
    name = "fake"

    def __init__(self, plan_lines: int = 3) -> None:
        self.calls = 0
        self.plan_lines = plan_lines

    async def complete(self, messages, max_tokens=0, **kw) -> str:
        self.calls += 1
        prompt = messages[-1]["content"]
        if "Break this goal into" in prompt:
            return "\n".join(f"{i}. step {i}" for i in range(1, self.plan_lines + 1))
        return f"answer ({self.calls})"

    async def stream(self, messages, system=None, **kw):
        self.calls += 1
        yield "streamed answer"


class FakeMemory:
    def __init__(self) -> None:
        self.writes: list = []

    async def search(self, q, scope="default", k=5):
        return []

    async def write(self, content, scope="default", metadata=None):
        self.writes.append(content)
        return f"mem_{len(self.writes)}"


class LowConfidenceVerifier:
    def __init__(self) -> None:
        self.calls = 0

    async def verify(self, goal_text, result_text):
        self.calls += 1
        return {"status": "verified", "confidence": 0.2, "verdict": "weak", "critique": "too thin"}


def _loop(gw, mem=None, verifier=None, policy="auto", ws=None):
    return GoalLoop(gw, RulesStrategist(gw), ws, memory=mem,
                    verifier=verifier, memory_policy=policy)


def _drive(loop, text):
    events = []

    async def on_event(ev):
        events.append(ev)

    receipt = asyncio.run(loop.run(text, on_event))
    return receipt, events


def main() -> None:
    # 1+2+3: the explicit, testable loop decision.
    r = _chat_route("hey, how are you today?")
    assert r["mode"] == "fast_chat" and r["useLoop"] is False, r
    r = _chat_route("what did I say my favorite coffee was")
    assert r["useLoop"] is False, r
    r = _chat_route("Plan a step by step launch strategy for the alpha, then verify each assumption against what you remember")
    assert r["mode"] == "goal_loop" and r["useLoop"] is True, r
    r = _chat_route("send the invoice to the client and publish the update")
    assert r["mode"] == "approval_required" and r["useLoop"] is False, r
    r = _chat_route("run workflow morning brief for me")
    assert r["mode"] == "approval_required", r

    # Regression 2026-06-12: a read-only critique was refused because bare
    # substrings ("send" in "sending", "pay " in "pay attention", "charge" in
    # "in charge of") tripped the action gate. Talking ABOUT actions is not
    # asking for one — critique-only turns must reach the goal loop.
    critique = (
        "Read-only thinking task, no actions needed: critique the philosophy "
        "of this essay on publishing. The author keeps sending the reader in "
        "circles, stays in charge of every emotionally charged scene, and asks "
        "us to pay attention to removed stanzas without buying the premise."
    )
    r = _chat_route(critique)
    assert r["mode"] == "goal_loop" and r["useLoop"] is True, r
    # The same protection must hold without the explicit read-only label.
    r = _chat_route(
        "critique the pacing here: the hero keeps sending letters, publishing "
        "manifestos, and paying attention to nothing"
    )
    assert r["mode"] == "goal_loop", r
    r = _chat_route("what would you remove from this draft?")
    assert r["mode"] != "approval_required", r
    # Genuine requests still gate: action verb in a request position.
    r = _chat_route("please publish this post to the blog")
    assert r["mode"] == "approval_required", r
    r = _chat_route("can you delete the staging database")
    assert r["mode"] == "approval_required", r
    print("OK loop decision: fast_chat / goal_loop / approval_required / critique not gated")

    with tempfile.TemporaryDirectory() as tmp:
        ws = LocalWorkspace(tmp)

        # 4: recursion stops at the hard cap even if the plan is huge.
        gw = FakeGateway(plan_lines=50)
        tactic = HierarchicalTactic(gw)
        cands = asyncio.run(tactic.run(Goal(text="big goal"), {}))
        steps = cands.candidates[0].meta["steps"]
        assert len(steps) <= 5, f"step cap violated: {len(steps)}"
        # 1 plan + <=5 steps + 1 synthesis = bounded calls
        assert gw.calls <= 7, f"unbounded calls: {gw.calls}"
        print(f"OK recursion cap: 50-line plan -> {len(steps)} steps, {gw.calls} calls")

        # 5: low-confidence verifier triggers exactly one repair.
        gw = FakeGateway()
        verifier = LowConfidenceVerifier()
        loop = _loop(gw, mem=FakeMemory(), verifier=verifier, ws=ws)
        receipt, _ = _drive(loop, "hi")
        assert verifier.calls == 1, verifier.calls
        assert receipt["verifier"]["repaired"] is True, receipt["verifier"]
        assert receipt["verifier"]["repairAttempts"] == 1, receipt["verifier"]
        print("OK verifier: low confidence -> exactly one repair pass")

        # 6: candidate policy proposes, never writes; auto policy writes.
        mem = FakeMemory()
        loop = _loop(FakeGateway(), mem=mem, policy="candidate", ws=ws)
        receipt, _ = _drive(loop, "hi")
        assert mem.writes == [], mem.writes
        assert len(receipt["memory"]["proposed"]) == 1, receipt["memory"]
        assert receipt["memory"]["written"] == [], receipt["memory"]

        mem = FakeMemory()
        loop = _loop(FakeGateway(), mem=mem, policy="auto", ws=ws)
        receipt, _ = _drive(loop, "hi")
        assert len(mem.writes) == 1, mem.writes
        assert len(receipt["memory"]["written"]) == 1, receipt["memory"]
        print("OK memory gate: candidate policy proposes only; auto policy writes")

        # 7: the receipt is truthful and complete.
        loop = _loop(FakeGateway(), mem=FakeMemory(), policy="candidate", ws=ws)
        receipt, events = _drive(loop, "hi")
        assert receipt["mode"] == "goal_loop" and receipt["loopUsed"] is True, receipt
        assert receipt["tactic"] == "solo" and receipt["agentsInvoked"] == ["solo"], receipt
        assert receipt["verifier"] == {"status": "skipped"}, receipt["verifier"]
        assert receipt["externalActions"] == [] and receipt["actionExecuted"] is False, receipt
        provenance = receipt["modelProvenance"]
        assert provenance["provider"] == "fake" and provenance["fallback_used"] is False, provenance
        assert any(ev.get("type") == "receipt" for ev in events), "no receipt event emitted"

        fallback_gw = FakeGateway()
        fallback_gw.requested_model = "primary-model"
        fallback_gw.resolved_model = "fallback-model"
        fallback_receipt, _ = _drive(
            _loop(fallback_gw, mem=FakeMemory(), policy="candidate", ws=ws), "hi"
        )
        fallback = fallback_receipt["modelProvenance"]
        assert fallback["requested_model"] == "primary-model", fallback
        assert fallback["resolved_model"] == "fallback-model", fallback
        assert fallback["fallback_used"] is True, fallback
        print("OK receipt: model provenance/fallback, tactic, verifier, memory, actions")

    print("\nsmoke_loop_receipts: ALL OK")


if __name__ == "__main__":
    main()
