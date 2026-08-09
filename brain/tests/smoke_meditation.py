"""Smoke — the meditation posture (kernel/core/meditation.py) and its wiring.

Proves four things, all with the echo provider (no real model):
  1. apply_to() appends the canonical prior on top of a base system prompt,
     and the prior is pure ASCII (house rule: no em dashes in shipped text).
  2. is_active()/activate()/deactivate() track the ENGRAM_MEDITATE env flag.
  3. The strategist PINS to solo when the posture is active — even for a goal
     that otherwise routes to council ("...decide...evaluate..."), which is the
     exact mis-route the flag fixes — and records posture in the budget. When
     inactive, routing is unchanged (the council goal still routes to council).
  4. The solo seam actually appends the prior to the system prompt when active,
     and does NOT when inactive (captured off a fake gateway).

Run from the repo root:  MODEL_PROVIDER=echo python tests/smoke_meditation.py
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MODEL_PROVIDER"] = "echo"
os.environ.pop("ENGRAM_MEDITATE", None)  # hermetic: never inherit an operator opt-in

from kernel.core import meditation  # noqa: E402
from kernel.core.strategist import RulesStrategist  # noqa: E402
from kernel.core.types import Goal  # noqa: E402
from tactics.solo import SoloTactic  # noqa: E402

# A goal that the keyword router sends to council (contains "decide"/"evaluate").
_COUNCIL_GOAL = ("Should we decide whether to evaluate the new pricing model for "
                 "the product this quarter and beyond")


class _CaptureGateway:
    """Records the `system` prompt handed to the model, so the test can assert
    what the solo seam composed without needing a real completion."""
    name = "echo"

    def __init__(self) -> None:
        self.system: str | None = None

    async def stream(self, messages, system=None, **kw):
        self.system = system
        yield "ok"

    async def complete(self, messages, **kw):  # unused on the no-connector path
        return "ok"


async def _solo_system(active: bool) -> str:
    """Run SoloTactic once (no connectors -> stream path) and return the system
    prompt it composed, with the posture toggled to `active`."""
    if active:
        meditation.activate()
    else:
        meditation.deactivate()
    gw = _CaptureGateway()

    async def _emit(_kind, _data):
        return None

    await SoloTactic(gw).run(
        Goal(text="say hello to the world in one short line"),
        {"emit": _emit, "memory": [], "connectors": None, "history": []},
    )
    meditation.deactivate()
    assert gw.system is not None, "solo did not compose a system prompt"
    return gw.system


async def main() -> None:
    # 1. apply_to appends the prior; result is pure ASCII (no em/en dashes).
    applied = meditation.apply_to("BASE PERSONA")
    assert "BASE PERSONA" in applied, "base prompt dropped"
    assert meditation.MEDITATION_PRIOR in applied, "prior not appended"
    applied.encode("ascii")  # raises if any non-ASCII slipped into the prior
    assert "—" not in applied and "–" not in applied, "em/en dash in prior"

    # 2. env flag round-trips.
    meditation.deactivate()
    assert meditation.is_active() is False, "should be inactive when unset"
    meditation.activate()
    assert meditation.is_active() is True, "activate() did not set the flag"
    os.environ["ENGRAM_MEDITATE"] = "true"
    assert meditation.is_active() is True, "'true' should count as active"
    meditation.deactivate()
    assert meditation.is_active() is False, "deactivate() did not clear the flag"

    # 3. strategist routing: inactive -> council; active -> pinned solo.
    strat = RulesStrategist(object())
    meditation.deactivate()
    off = await strat.choose(Goal(text=_COUNCIL_GOAL), {})
    assert off.tactic.name == "council_debate", f"baseline route changed: {off.tactic.name}"
    assert "posture" not in off.budget, "posture leaked when inactive"

    meditation.activate()
    try:
        on = await strat.choose(Goal(text=_COUNCIL_GOAL), {})
        assert on.tactic.name == "solo", f"meditation did not pin solo: {on.tactic.name}"
        assert on.budget.get("posture") == meditation.PRIOR_ID, f"posture not in budget: {on.budget}"
        # A short conversational turn is also pinned to solo (would be solo anyway,
        # but confirms the pin wins ahead of every rule).
        short = await strat.choose(Goal(text="make it shorter"), {})
        assert short.tactic.name == "solo" and short.budget.get("posture") == meditation.PRIOR_ID
    finally:
        meditation.deactivate()

    # 4. solo seam applies / omits the prior by flag.
    sys_active = await _solo_system(active=True)
    assert meditation.MEDITATION_PRIOR in sys_active, "solo did not apply the prior when active"
    sys_inactive = await _solo_system(active=False)
    assert meditation.MEDITATION_PRIOR not in sys_inactive, "solo applied the prior when inactive"

    # Leave the process clean for any downstream import.
    meditation.deactivate()

    print("MEDITATION SMOKE: OK")
    print(f"  prior id    : {meditation.PRIOR_ID}")
    print("  apply_to    : appends prior, pure ASCII")
    print("  env flag    : activate/deactivate/is_active round-trip")
    print("  strategist  : council goal PINNED to solo when active (else council)")
    print("  solo seam   : prior present iff active")


if __name__ == "__main__":
    asyncio.run(main())
