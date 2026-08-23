"""The positive-expectation prior — the study's manipulated variable.

This is the single source of truth for the "innately positive result
expectation" control used in the *A Search for Understanding* study
(`research/consciousness-study/`). It is kept here, version-controlled, so the
control is reproducible and citable rather than a vibe.

IMPORTANT — what this is and is not:
- It is NOT optimism about outcomes, and it does NOT loosen the honesty floor in
  `kernel/core/persona.py` ("Be honest and direct. Don't hype or flatter.").
  Reporting stays truthful: failures are reported as failures.
- It IS an *agentic self-efficacy* prior: approach each goal as solvable, take
  initiative, follow through, and keep state current (notes, graph, memory)
  without being reminded. The hypothesis is that an efficacy prior measurably
  improves follow-through, memory formation, and self-direction.

Application status (honest): TODAY this prior is *operator-applied* — the owner sets
the expectation in how he opens and runs sessions. It is not auto-injected into
the global persona, on purpose: any product persona must
not change as a side effect of the study. A surface that wants the prior
(e.g. the operator TUI) can opt in by appending `POSITIVE_EXPECTATION_PRIOR`
to the system prompt; the study records whether it was active per snapshot.
"""

from __future__ import annotations

# Canonical text of the control. Append to a system prompt to apply it.
POSITIVE_EXPECTATION_PRIOR = """Operating expectation for this session:

Treat every goal as solvable and approach it with initiative. You do not need to
be reminded to do the obvious supporting work — keep notes live, keep the
knowledge graph and memory current, reach for the right tool or connector
yourself, and carry a task to a verified finish.

This is an expectation of *effort and follow-through*, never of outcome. Stay
fully honest: if something fails, is blocked, or is uncertain, say so plainly.
Confidence is about how you work, not about gilding the result."""

# Stable id + short label for snapshots and the ledger.
PRIOR_ID = "positive-expectation-v1"
PRIOR_LABEL = "agentic self-efficacy prior (effort, not outcome)"


def apply_to(system_prompt: str) -> str:
    """Return a system prompt with the prior appended. Opt-in, never automatic."""
    return f"{system_prompt.rstrip()}\n\n{POSITIVE_EXPECTATION_PRIOR}\n"
