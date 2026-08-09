"""Resolvers — collapse N candidates into ONE decisive result.

- SingleResolver: pass-through (one candidate -> that result). The loop pairs it
  with every tactic; swarm tactics collapse internally (below) and hand the loop
  the already-decided single answer, so SingleResolver just forwards it.
- JudgeResolver: LLM-as-judge picks/refines the best of N (parallel_explore).
- AggregatorResolver: synthesize N debated positions into one (council_debate).
JudgeResolver/AggregatorResolver are gateway-backed and stream the final answer
via `emit`, so the decisive answer streams to the surface.
"""

from __future__ import annotations

from typing import Any

from kernel.contracts import Resolver
from kernel.core.types import CandidateSet, DecisiveResult, Goal


class SingleResolver(Resolver):
    async def resolve(self, candidates: CandidateSet, goal: Goal) -> DecisiveResult:
        if not candidates.candidates:
            return DecisiveResult(text="(no candidates produced)", confidence=0.0, tactic="solo")
        c = candidates.candidates[0]
        return DecisiveResult(text=c.text, confidence=1.0, rationale="single candidate", tactic="solo", model=c.model)


class JudgeResolver(Resolver):
    """Pick the best of N candidates and return the strongest final answer."""

    def __init__(self, gateway: Any) -> None:
        self.gateway = gateway

    async def resolve(self, candidates: CandidateSet, goal: Goal, emit=None) -> DecisiveResult:
        cands = candidates.candidates
        if not cands:
            return DecisiveResult(text="(no candidates)", confidence=0.0, tactic="parallel_explore")
        if len(cands) == 1:
            return DecisiveResult(text=cands[0].text, confidence=1.0, tactic="parallel_explore", model=cands[0].model)
        listing = "\n\n".join(f"--- Candidate {i + 1} ---\n{c.text}" for i, c in enumerate(cands))
        prompt = (
            f"Goal: {goal.text}\n\nHere are {len(cands)} candidate answers:\n\n{listing}\n\n"
            "Choose the single best one and return the strongest possible final answer "
            "(you may refine the winner). Return only the final answer."
        )
        parts: list[str] = []
        async for tok in self.gateway.stream([{"role": "user", "content": prompt}]):
            parts.append(tok)
            if emit:
                await emit("token", tok)
        return DecisiveResult(text="".join(parts).strip(), confidence=0.8,
                              rationale=f"judged best of {len(cands)}", tactic="parallel_explore",
                              model=getattr(self.gateway, "name", ""))


class AggregatorResolver(Resolver):
    """Synthesize N debated council positions into one decisive answer."""

    def __init__(self, gateway: Any) -> None:
        self.gateway = gateway

    async def resolve(self, candidates: CandidateSet, goal: Goal, emit=None) -> DecisiveResult:
        cands = candidates.candidates
        if not cands:
            return DecisiveResult(text="(no positions)", confidence=0.0, tactic="council_debate")
        listing = "\n\n".join(f"--- Member {i + 1} ---\n{c.text}" for i, c in enumerate(cands))
        prompt = (
            f"Goal: {goal.text}\n\nA council debated and reached these final positions:\n\n{listing}\n\n"
            "Synthesize them into one clear, decisive final answer, resolving the disagreements. "
            "Return only the final answer."
        )
        parts: list[str] = []
        async for tok in self.gateway.stream([{"role": "user", "content": prompt}]):
            parts.append(tok)
            if emit:
                await emit("token", tok)
        return DecisiveResult(text="".join(parts).strip(), confidence=0.85,
                              rationale=f"council of {len(cands)} -> consensus", tactic="council_debate",
                              model=getattr(self.gateway, "name", ""))
