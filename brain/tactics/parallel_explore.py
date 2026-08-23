"""ParallelExploreTactic — best-of-N: N independent attempts (one model), pick the best.

Not a debate, not multiple vendors ganging up: it's ONE model trying the goal N
ways in parallel, then a judge picking/refining the strongest. For generative
goals where quality matters and you can tell a good answer from a weak one.
"""

from __future__ import annotations

import asyncio
from typing import Any

from kernel.contracts import Tactic
from kernel.core.types import Candidate, CandidateSet, Goal
from tactics.resolver import JudgeResolver
from tactics._context import convo_preamble, memory_preamble

_ANGLES = [
    "Be bold and unconventional.",
    "Be careful, simple, and practical.",
    "Optimize for the highest quality, even if it takes more.",
    "Optimize for speed and the 80/20.",
    "Take an angle the others would miss.",
]


class ParallelExploreTactic(Tactic):
    name = "parallel_explore"

    def __init__(self, gateway: Any, n: int = 3) -> None:
        self.gateway = gateway
        self.n = max(2, min(n, len(_ANGLES)))

    def estimate(self, goal: Goal, ctx: Any) -> dict[str, Any]:
        return {"calls": self.n + 1, "risk": "medium"}

    async def _attempt(self, goal: Goal, angle: str, mem: str) -> str:
        out = await self.gateway.complete(
            [{"role": "user", "content": f"{mem}Goal: {goal.text}\n\nApproach: {angle}\nGive your best complete answer."}],
            max_tokens=1024,
        )
        return out.strip()

    async def run(self, goal: Goal, ctx: Any) -> CandidateSet:
        ctx = ctx or {}
        emit = ctx.get("emit")
        memory = ctx.get("memory") or []
        mem = convo_preamble(ctx)
        if memory:
            mem += memory_preamble(memory)

        if emit:
            await emit("status", f"exploring {self.n} approaches in parallel …")
        # return_exceptions: one transient attempt failure (rate limit, 5xx) must
        # not throw away the sibling attempts already paid for — judge whatever
        # survived, and only fail loud when every attempt failed.
        raw = await asyncio.gather(*[self._attempt(goal, a, mem) for a in _ANGLES[: self.n]], return_exceptions=True)
        outs = [o for o in raw if not isinstance(o, BaseException)]
        dropped = len(raw) - len(outs)
        if not outs:
            raise next(o for o in raw if isinstance(o, BaseException))
        if dropped and emit:
            await emit("status", f"{dropped} attempt(s) failed and were dropped …")
        cands = CandidateSet(candidates=[Candidate(text=o, model=getattr(self.gateway, "name", "")) for o in outs])

        if emit:
            await emit("status", "judging candidates for the best …")
        result = await JudgeResolver(self.gateway).resolve(cands, goal, emit)
        # Receipts stay honest: fewer attempts than advertised is a weaker
        # best-of-N, so the drop count rides in the meta.
        return CandidateSet(candidates=[Candidate(text=result.text, model=result.model, meta={"explored": self.n, "attempts_dropped": dropped})])
