"""The meditation posture — sit before you commit.

A version-controlled *posture prior*, the sibling of the positive-expectation
prior in `kernel/core/expectation.py`. Where the expectation prior changes the
brain's *effort* stance, meditation changes its *commitment* stance: ingest the
whole input, do not begin while still weighing it, hold it until the essential
move surfaces, then return the smallest true change and let the rest go in
silence.

IMPORTANT — what this is and is not:
- It is NOT mysticism, and it does NOT loosen the honesty floor in
  `kernel/core/persona.py`. "Inspiration" never licenses hand-waving: a claim
  that cannot be grounded is still cut, and a failure is still named a failure.
- It IS an incubation-before-output posture: resist the reflex to answer point
  by point, let the essential move settle, and act only on what survives.

Application status (honest): TODAY this is a *system-prompt posture* applied at
the single-agent seam (`tactics/solo.py`) when opted in via `--meditate` or the
`ENGRAM_MEDITATE` env var. It is opt-in, never automatic, so the product
product persona is unaffected. When active, the strategist pins
the tactic to `solo` (`kernel/core/strategist.py`) — that keeps the posture
reliably applied and stops the keyword router from mis-routing a meditative
prompt on stray words like "assess" or "decide" that appear inside the
instruction itself. Because it is a posture of *thinking* (not tool-calling),
the solo seam ALSO suppresses connectors while it is active (`tactics/solo.py`):
the run is a single streamed completion, never the non-streaming, up-to-5-call
tool loop — which emits nothing until it finishes and, with a stuck connector,
could hang the pinned-solo run. This module remains that prompt posture. The
separate `thought-bus-meditation-v1` policy is the first mechanical commitment
boundary: after the final Thought Bus pass, it proposes zero or one intent and
otherwise abstains. Because this posture mirrors the expectation prior, it can
be A/B-measured the same way in the study harness.
"""

from __future__ import annotations

import os

# Canonical text of the posture. Append to a system prompt to apply it.
MEDITATION_PRIOR = """Operating posture for this turn: meditation.

Ingest the whole input before you form a response. Do not begin while you are
still weighing it. Hold it until the essential move surfaces on its own, then
act only on what survives.

When you act, return the smallest set of changes that make the work truer, not a
point-by-point reply to every remark. Take what is right without ceremony. Let
what is wrong go in silence, without arguing it down.

This is a posture of restraint, not of license. Stay fully honest: inspiration
never excuses a claim you cannot ground, and a failure is still named a failure.
Sitting longer is for finding the true change, never for softening the truth."""

# Stable id + short label for the receipt, the ledger, and study snapshots.
PRIOR_ID = "meditation-v1"
PRIOR_LABEL = "incubation-before-output posture (sit, then the smallest true change)"

_ENV_FLAG = "ENGRAM_MEDITATE"
_TRUE = frozenset({"1", "true", "yes", "on"})


def is_active() -> bool:
    """True when the meditation posture is opted in for this process (env flag)."""
    return (os.environ.get(_ENV_FLAG, "") or "").strip().lower() in _TRUE


def activate() -> None:
    """Opt this process into the meditation posture. The CLI `--meditate` seam."""
    os.environ[_ENV_FLAG] = "1"


def deactivate() -> None:
    """Clear the meditation opt-in for this process (used by tests/surfaces)."""
    os.environ.pop(_ENV_FLAG, None)


def apply_to(system_prompt: str) -> str:
    """Return a system prompt with the meditation posture appended. Opt-in, never automatic."""
    return f"{system_prompt.rstrip()}\n\n{MEDITATION_PRIOR}\n"
