"""CouncilDebateTactic — a council debates to a decisive answer. One model, N voices.

For high-stakes judgment calls. N members (distinct personas of the SAME model)
state positions, then see each other's views and revise (the debate), then an
aggregator synthesizes one decisive answer that resolves the disagreement. Not
multi-vendor — one model wearing N hats — per the "no models ganging up" rule.
"""

from __future__ import annotations

import asyncio
from typing import Any

from kernel.contracts import Tactic
from kernel.core.types import Candidate, CandidateSet, Goal
from tactics.resolver import AggregatorResolver
from tactics._context import convo_preamble, memory_preamble

_PERSONAS = [
    "an optimist who sees the upside and the opportunity",
    "a skeptic who probes the risks and what could go wrong",
    "a pragmatist focused on what actually works in practice",
    "a long-term thinker weighing the two-year consequences",
]


class CouncilDebateTactic(Tactic):
    name = "council_debate"

    def __init__(self, gateway: Any, n: int = 3) -> None:
        self.gateway = gateway
        self.n = max(2, min(n, len(_PERSONAS)))

    def estimate(self, goal: Goal, ctx: Any) -> dict[str, Any]:
        return {"calls": self.n * 2 + 1, "risk": "high"}

    async def _voice(self, goal: Goal, persona: str, others: list[str], mem: str) -> str:
        debate = ""
        if others:
            debate = "Other members said:\n" + "\n\n".join(f"- {o}" for o in others) + "\n\nConsidering them, refine your view. "
        out = await self.gateway.complete(
            [{"role": "user", "content": f"{mem}You are {persona} on a council. Goal: {goal.text}\n\n{debate}Give your position and your reasoning, concisely."}],
            max_tokens=700,
        )
        return out.strip()

    async def run(self, goal: Goal, ctx: Any) -> CandidateSet:
        ctx = ctx or {}
        emit = ctx.get("emit")
        memory = ctx.get("memory") or []
        mem = convo_preamble(ctx)
        if memory:
            mem += memory_preamble(memory)
        personas = _PERSONAS[: self.n]

        if emit:
            await emit("status", f"convening a council of {self.n} …")
        # return_exceptions: one transient voice failure (rate limit, 5xx) must
        # not discard the sibling completions already paid for — drop the failed
        # voice, keep debating with the survivors, and only fail loud when the
        # whole council failed.
        r1 = await asyncio.gather(*[self._voice(goal, p, [], mem) for p in personas], return_exceptions=True)
        survivors = [(p, r) for p, r in zip(personas, r1) if not isinstance(r, BaseException)]
        dropped = len(r1) - len(survivors)
        if not survivors:
            raise next(r for r in r1 if isinstance(r, BaseException))
        if dropped and emit:
            await emit("status", f"{dropped} voice(s) failed and were dropped …")

        if emit:
            await emit("status", "debate: members respond to each other …")
        round1 = [r for _, r in survivors]
        r2 = await asyncio.gather(
            *[self._voice(goal, survivors[i][0], [r for j, r in enumerate(round1) if j != i], mem) for i in range(len(survivors))],
            return_exceptions=True,
        )
        round2 = [r for r in r2 if not isinstance(r, BaseException)]
        dropped += len(r2) - len(round2)
        if not round2:
            raise next(r for r in r2 if isinstance(r, BaseException))
        cands = CandidateSet(candidates=[Candidate(text=r, model=getattr(self.gateway, "name", "")) for r in round2])

        if emit:
            await emit("status", "synthesizing a decisive answer …")
        result = await AggregatorResolver(self.gateway).resolve(cands, goal, emit)
        # Receipts stay honest: a synthesis over fewer voices than convened is
        # a different (weaker) artifact, so the drop count rides in the meta.
        return CandidateSet(candidates=[Candidate(text=result.text, model=result.model, meta={"council": self.n, "voices_dropped": dropped})])
