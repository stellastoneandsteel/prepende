"""Strategist / Tactic / Resolver — the "decide HOW to think" layer.

Early in the Goal Loop, before committing effort, the brain chooses an
execution TACTIC for the goal: solo, hierarchical (manager-worker), a
council/debate that argues to a decisive conclusion, parallel exploration
(best-of-N), or a fixed pipeline. This is the "swarm decisiveness" layer —
it can convene many agents/perspectives and resolve them into ONE decisive
result.

This whole layer is OURS and closed-source — it is crown-jewel IP and there
is no license risk in owning it. Permissively-licensed frameworks
(openai-agents-python MIT, agent-squad Apache-2.0, agent-framework/Magentic
MIT, MoA Apache-2.0, langgraph MIT) may be borrowed as swappable primitives
*inside* a Tactic implementation — never as the spine, never anything
copyleft/AGPL or source-available-with-non-compete (AutoGPT's Polyform,
unlicensed debate code).

Flow:  Goal -> Strategist.choose() -> Tactic.run() -> Resolver.resolve() -> DecisiveResult

Impl: tactics/   Used by: kernel/core/ (the Goal Loop)
SKELETON — signatures only, no implementation yet.

In a real impl, Goal / Context / CandidateSet / DecisiveResult / Budget are
typed (Pydantic) models; here they are Any to keep the skeleton minimal.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class Tactic(ABC):
    """One coordination topology. Produces 1..N candidate outputs (+ traces)."""

    name: str

    @abstractmethod
    def estimate(self, goal: Any, ctx: Any) -> Any:
        """Cheap cost/latency/risk estimate so the Strategist can choose."""

    @abstractmethod
    async def run(self, goal: Any, ctx: Any) -> Any:
        """Execute the topology. Returns a CandidateSet (one or many candidates)."""


class Resolver(ABC):
    """The decisiveness collapse: many candidates -> exactly one result.

    Modes (pick by tactic): verifier (objective gate) > aggregator
    (synthesize one, MoA-style) > judge/vote (LLM-as-judge or weighted/
    Bayesian vote). Every resolve() must emit a confidence — low confidence
    feeds back to the Strategist to escalate.
    """

    @abstractmethod
    async def resolve(self, candidates: Any, goal: Any) -> Any:
        """Return one DecisiveResult (output + rationale + confidence)."""


class Strategist(ABC):
    """The meta-router. Runs EARLY in the Goal Loop to pick tactic + resolver.

    v1 is rules-based on cheap signals (ambiguity, decomposability, presence
    of an objective verifier, stakes/reversibility, budget) with an
    escalate-on-low-confidence policy: start solo, escalate to heavier
    tactics only on low confidence or failure. Logs every choice so a learned
    Strategist can be earned later.
    """

    @abstractmethod
    async def choose(self, goal: Any, ctx: Any) -> Any:
        """Return the chosen {tactic, resolver, budget} for this goal."""
