"""HierarchicalTactic — manager-worker: decompose a goal, work each step, synthesize.

This is the workhorse for vague, open-ended goals — the "figure out how to
pursue it" capability. It runs ONE model across multiple steps (no council, no
multiple models ganging up): plan -> do each step (each sees prior work) ->
synthesize one answer. Emits the plan and per-step progress so the surface can
show a living plan, separate from the conversation.

Falls back to a single step if the goal isn't decomposable (e.g. on the echo
provider), so it degrades gracefully.
"""

from __future__ import annotations

import re
from typing import Any

from kernel.contracts import Tactic
from kernel.core.types import Candidate, CandidateSet, Goal
from tactics._context import convo_preamble, memory_preamble


class HierarchicalTactic(Tactic):
    name = "hierarchical"

    def __init__(self, gateway: Any) -> None:
        self.gateway = gateway

    def estimate(self, goal: Goal, ctx: Any) -> dict[str, Any]:
        return {"calls": "2..6", "risk": "medium"}

    async def _ask(self, prompt: str) -> str:
        return await self.gateway.complete([{"role": "user", "content": prompt}], max_tokens=1024)

    @staticmethod
    def _parse_steps(text: str) -> list[str]:
        # Only TOP-LEVEL list lines become steps. Models routinely elaborate a
        # numbered plan with indented sub-bullets; promoting those to standalone
        # steps distorts the order and evicts real steps at the 5-step cap, so
        # sub-items fold into their parent step instead of becoming their own.
        items: list[tuple[int, bool, str]] = []  # (indent, is_numbered, body)
        for line in text.splitlines():
            m = re.match(r"(\s*)(\d+[.)]|[-*])\s+(.*)", line)
            if m and m.group(3).strip():
                items.append((len(m.group(1).expandtabs()), m.group(2)[0].isdigit(), m.group(3).strip()))
        if not items:
            return []
        # Prefer numbered lines as the step skeleton (the plan prompt asks for
        # them); bullets only carry the plan when no numbered lines exist at all.
        use_numbered = any(num for _, num, _ in items)
        top = min(ind for ind, num, _ in items if num == use_numbered)
        steps: list[str] = []
        for ind, num, body in items:
            if num == use_numbered and ind <= top:
                steps.append(body)
            elif steps:
                # Sub-bullet / deeper item: keep the detail with its parent.
                steps[-1] += f" ({body})"
        return steps

    async def run(self, goal: Goal, ctx: Any) -> CandidateSet:
        ctx = ctx or {}
        emit = ctx.get("emit")
        memory = ctx.get("memory") or []
        mem = convo_preamble(ctx)
        if memory:
            mem += memory_preamble(memory)

        # 1. Plan — decompose into ordered steps.
        plan_text = await self._ask(
            f"{mem}Break this goal into 2-5 concrete, ordered steps to actually accomplish it. "
            f"One step per line, numbered. Goal: {goal.text}"
        )
        steps = self._parse_steps(plan_text)
        steps = steps[:5]  # hard cap — recursion stops at the budget no matter what the plan says
        if len(steps) < 2:
            steps = [goal.text]  # not decomposable -> treat as one step (graceful)
        if emit:
            await emit("status", f"plan ({len(steps)} steps):")
            for i, s in enumerate(steps, 1):
                await emit("status", f"  {i}. {s[:80]}")

        # 2. Work each step (each sees the work so far).
        done: list[str] = []
        for i, s in enumerate(steps, 1):
            if emit:
                await emit("status", f"step {i}/{len(steps)} …")
            prior = ("Work so far:\n" + "\n\n".join(done) + "\n\n") if done else ""
            out = await self._ask(f"{mem}{prior}Goal: {goal.text}\n\nDo ONLY this step and return its result:\n{s}")
            done.append(f"## {s}\n{out.strip()}")

        # 3. Synthesize one final answer (streamed).
        synth = (
            f"{mem}Goal: {goal.text}\n\nWork from each step:\n\n" + "\n\n".join(done)
            + "\n\nSynthesize this into one clear, complete final answer."
        )
        parts: list[str] = []
        async for tok in self.gateway.stream([{"role": "user", "content": synth}]):
            parts.append(tok)
            if emit:
                await emit("token", tok)
        final = "".join(parts).strip()
        return CandidateSet(candidates=[Candidate(text=final, model=getattr(self.gateway, "name", ""), meta={"steps": steps})])
