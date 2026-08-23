"""Phase 2 smoke — the Strategist escalates project-shaped goals to hierarchical,
and the hierarchical tactic runs (plan -> steps -> synthesis) end to end. Zero infra.
    python tests/smoke_phase2.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kernel.core.strategist import RulesStrategist  # noqa: E402
from kernel.core.types import Goal  # noqa: E402
from kernel.core.loop import GoalLoop  # noqa: E402
from models.echo import EchoGateway  # noqa: E402
from workspace.local import LocalWorkspace  # noqa: E402
from tactics.solo import SoloTactic  # noqa: E402
from tactics.hierarchical import HierarchicalTactic  # noqa: E402


async def main() -> None:
    gw = EchoGateway()
    strat = RulesStrategist(gw)

    # Routing: a short question stays solo; a project-shaped goal escalates.
    simple = await strat.choose(Goal(text="what time is it in Tokyo"), {})
    assert isinstance(simple.tactic, SoloTactic), f"expected solo, got {type(simple.tactic).__name__}"
    project = await strat.choose(Goal(text="help me build a plan to launch my product to early users"), {})
    assert isinstance(project.tactic, HierarchicalTactic), f"expected hierarchical, got {type(project.tactic).__name__}"

    # Hierarchical runs end to end and emits a plan.
    tmp = tempfile.mkdtemp(prefix="engram_p2_")
    loop = GoalLoop(gw, strat, LocalWorkspace(os.path.join(tmp, "ws")))
    events: list[dict] = []
    await loop.run("research and lay out a step by step roadmap to open a coffee shop", lambda ev: events.append(ev) or asyncio.sleep(0))
    statuses = [e["text"] for e in events if e["type"] == "status"]
    assert any("hierarchical" in s for s in statuses), f"did not run hierarchical: {statuses}"
    assert any(s.startswith("plan") for s in statuses), f"no plan emitted: {statuses}"
    assert any(e["type"] == "artifact" for e in events), "no artifact"
    assert any(e["type"] == "done" for e in events), "did not finish"

    print("PHASE 2 SMOKE: OK")
    print("  routing : short -> solo, project -> hierarchical")
    print(f"  hierarchical emitted plan + finished ({sum(e['type']=='token' for e in events)} tokens synthesized)")


if __name__ == "__main__":
    asyncio.run(main())
