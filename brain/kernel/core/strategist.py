"""RulesStrategist — decides HOW to think, early in the Goal Loop.

Phase 0: always choose `solo` (it's the only tactic yet), but the decision
point exists from day one. In Phase 2, this becomes a rules-based router over
cheap signals (ambiguity, decomposability, verifiability, stakes, budget) that
starts solo and escalates to hierarchical / council / parallel-explore only on
low confidence or failure. A learned router can be earned later from the logs.
"""

from __future__ import annotations

import re
from typing import Any

from kernel.contracts import Strategist
from kernel.core import meditation
from kernel.core.types import Choice, Goal
from tactics.solo import SoloTactic
from tactics.hierarchical import HierarchicalTactic
from tactics.parallel_explore import ParallelExploreTactic
from tactics.council import CouncilDebateTactic
from tactics.resolver import SingleResolver

# High-stakes judgment calls -> convene a council (debate -> decide).
_DECISION_PHRASES = (
    "should i", "should we", "is it worth", "worth it", "decide", "decision",
    "evaluate", "assess", "which is better", "pros and cons", "trade-off", "tradeoff",
    "risk of", "is it a good idea", "go or no",
)
# Generative goals where quality matters -> explore N, pick the best.
_GENERATIVE_PHRASES = (
    "brainstorm", "ideas for", "options for", "come up with", "draft", "write a",
    "name ideas", "names for", "variations", "alternatives", "pitch", "tagline",
)
# Multi-step projects -> decompose (hierarchical).
_PROJECT_PHRASES = (
    "step by step", "step-by-step", "break down", "break it down", "plan ",
    "roadmap", "strategy", "launch", "build a", "build me", "research ",
    "how do i", "how should i", "figure out", "walk me through",
)


def _compile_phrases(phrases: tuple[str, ...]) -> tuple[re.Pattern[str], ...]:
    """Word-boundary matchers for the routing phrases. Bare substring checks
    misrouted ordinary turns into multi-call swarms ('overdraft' contains
    'draft', 'assessment' contains 'assess'); anchor with \\b only where the
    phrase edge is a word character — phrases like 'plan ' or 'risk of'
    already carry their own boundary at the space."""
    compiled = []
    for p in phrases:
        pat = re.escape(p)
        if p[:1].isalnum():
            pat = r"\b" + pat
        if p[-1:].isalnum():
            pat = pat + r"\b"
        compiled.append(re.compile(pat))
    return tuple(compiled)


_DECISION_RES = _compile_phrases(_DECISION_PHRASES)
_GENERATIVE_RES = _compile_phrases(_GENERATIVE_PHRASES)
_PROJECT_RES = _compile_phrases(_PROJECT_PHRASES)


class RulesStrategist(Strategist):
    """v1: rules-based router over cheap signals. Default solo; escalate to a
    heavier tactic only when the goal warrants it (conservative — swarms cost
    several model calls). Order: judgment-call -> council, generative -> parallel,
    project -> hierarchical, else solo. Logs its choice so a learned router can be
    earned from the data. Swarms use ONE model (N voices), not multiple vendors."""

    def __init__(self, gateway: Any) -> None:
        self.gateway = gateway

    def _is_project(self, t: str) -> bool:
        return any(r.search(t) for r in _PROJECT_RES) or len(t.split()) >= 18

    async def choose(self, goal: Goal, ctx: Any) -> Choice:
        t = goal.text.lower()
        words = len(t.split())
        # Meditation posture (opt-in, --meditate / PREPENDE_MEDITATE) PINS the tactic
        # to solo. The posture text itself is applied at the solo seam
        # (tactics/solo.py); pinning here keeps it reliably applied AND stops the
        # keyword router below from mis-routing a meditative prompt on stray words
        # like "assess" or "decide" that appear inside the instruction. The receipt
        # records the posture. Checked before every other rule so it always wins.
        if meditation.is_active():
            med_tactic = SoloTactic(self.gateway)
            med_budget = {"max_calls": 1, "posture": meditation.PRIOR_ID}
            med_budget.update(self._registry_meta(med_tactic, ctx))
            return Choice(med_tactic, SingleResolver(), med_budget)
        # Conversational guard: short turns are almost always follow-ups
        # ("what should I name it", "yes", "make it shorter", "tell me more").
        # Keep them SOLO — fast, and the only path that carries chat history —
        # so the brain feels like a conversation, not a one-shot oracle. Heavy
        # tactics need a substantial, self-contained ask to fire.
        #
        # The rule order below IS the routing decision and must not change. The
        # registry lookup afterwards only ATTACHES metadata to the already-chosen
        # tactic; it never changes which tactic is selected.
        if words < 12:
            tactic, max_calls = SoloTactic(self.gateway), 1
        elif any(r.search(t) for r in _DECISION_RES):
            tactic, max_calls = CouncilDebateTactic(self.gateway), 7
        elif any(r.search(t) for r in _GENERATIVE_RES):
            tactic, max_calls = ParallelExploreTactic(self.gateway), 4
        elif self._is_project(t):
            tactic, max_calls = HierarchicalTactic(self.gateway), 6
        else:
            tactic, max_calls = SoloTactic(self.gateway), 1
        budget: dict[str, Any] = {"max_calls": max_calls}
        budget.update(self._registry_meta(tactic, ctx))
        return Choice(tactic, SingleResolver(), budget)

    def _registry_meta(self, tactic: Any, ctx: Any) -> dict[str, Any]:
        """Attach (log) the registry entry for the already-chosen tactic. Pure
        metadata: it does NOT affect which tactic was selected. If no registry is
        passed in ctx, returns {} and behavior is unchanged."""
        registry = ctx.get("registry") if isinstance(ctx, dict) else None
        if registry is None:
            return {}
        try:
            entry = registry.get(f"tactic.{getattr(tactic, 'name', '')}")
        except Exception:
            entry = None
        if entry is None:
            return {}
        return {
            "registryEntryId": entry.id,
            "orchestrationMode": entry.name,
            "readiness": entry.readiness,
            "externalActions": entry.external_actions,
            "approvalRequired": entry.approval_required,
            "estimate": dict(entry.estimate),
        }
