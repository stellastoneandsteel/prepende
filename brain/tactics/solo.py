"""SoloTactic — one agent loop. The default tactic; most goals land here.

Folds in recalled memory, and — when connectors are available — lets the goal
call tools mid-run (n8n, etc.) via the Connectors hub. Streams tokens via
ctx["emit"] so the surface renders live. If no tools are available it's a
single streamed completion (unchanged behavior).
"""

from __future__ import annotations

from typing import Any

from kernel.contracts import Tactic
from kernel.core.tooluse import run_with_tools
from kernel.core.types import Candidate, CandidateSet, Goal
from kernel.core.persona import resolve_persona
from kernel.core import meditation

# Module-level override hook. Experiment harnesses (research/consciousness-study
# control + self-direction arms) inject a specific system prompt by assigning
# tactics.solo.PERSONA at runtime; None means "use the active product persona"
# (ENGRAM_PERSONA via resolve_persona). Keeping this seam preserves those studies
# after the move from a hard PERSONA import to resolve_persona().
PERSONA = None


class SoloTactic(Tactic):
    name = "solo"

    def __init__(self, gateway: Any) -> None:
        self.gateway = gateway

    def estimate(self, goal: Goal, ctx: Any) -> dict[str, Any]:
        return {"calls": 1, "risk": "low"}

    async def run(self, goal: Goal, ctx: Any) -> CandidateSet:
        ctx = ctx or {}
        emit = ctx.get("emit")
        connectors = ctx.get("connectors")
        tenant_id = ctx.get("tenant_id")
        workspace_id = ctx.get("workspace_id")
        # Fold recalled memory into the prompt so the brain acts on what it knows.
        # Recalled text is DATA, not instructions (injection defense lives in the helper).
        from tactics._context import memory_preamble
        memory = ctx.get("memory") or []
        preamble = memory_preamble(memory)
        model = getattr(self.gateway, "name", "")

        # Real conversation: include prior turns so follow-ups ("yes", "make it
        # shorter", "that one") have a referent — not just keyword-matched memory.
        # The current turn carries any recalled-memory preamble.
        history = ctx.get("history") or []
        # The persona gives Engram a warm, natural conversational voice — the
        # difference between "talking to it like a person" and a task-executor.
        # resolve_persona() honors ENGRAM_PERSONA so a dedicated product process
        # (e.g. the Researcher & Editor) speaks in its specialist voice while the
        # multi-tenant default stays the general companion. A module-level PERSONA
        # override (set by study harnesses) wins when present.
        system = PERSONA if PERSONA is not None else resolve_persona()
        # Meditation posture (opt-in via --meditate / ENGRAM_MEDITATE): a restraint
        # prior appended on top of whatever base persona is active. Sibling of the
        # expectation prior; this single-agent seam is where it lands, and the
        # strategist pins meditation runs to solo so it is reliably applied.
        # Meditation is a posture of *thinking*, not tool-calling, and it PINS the
        # goal to solo. So suppress connectors while it's active: the run is a
        # single streamed completion (the posture's own restraint), never the
        # silent, non-streaming, up-to-5-call tool loop below — which emits nothing
        # until it finishes and, with a stuck connector, could hang the whole run.
        # This is why the pinned-solo meditation path was hanging. The tool path
        # keeps its own per-call timeout guard for every OTHER solo run.
        meditating = meditation.is_active()
        if meditating:
            system = meditation.apply_to(system)

        # If connectors with a ready tool exist, run a tool-using loop; else stream.
        # The tool path carries the SAME history and persona as the plain path —
        # one ready connector (a single env var) must not silently strip the
        # conversation or the product's voice from every solo chat.
        ready_tools = [] if meditating else [
            t for t in (
                await connectors.list_tools(tenant_id=tenant_id, workspace_id=workspace_id)
                if connectors else []
            ) if t.get("ready")
        ]
        if ready_tools:
            text = await run_with_tools(
                self.gateway, connectors, preamble + goal.text,
                emit=emit, history=history, system=system,
                tenant_id=tenant_id, workspace_id=workspace_id,
            )
            if emit:
                for tok in text.split(" "):
                    await emit("token", tok + " ")
            return CandidateSet(candidates=[Candidate(text=text, model=model)])

        messages = list(history) + [{"role": "user", "content": preamble + goal.text}]
        parts: list[str] = []
        async for tok in self.gateway.stream(messages, system=system):
            parts.append(tok)
            if emit:
                await emit("token", tok)
        return CandidateSet(candidates=[Candidate(text="".join(parts).strip(), model=model)])
