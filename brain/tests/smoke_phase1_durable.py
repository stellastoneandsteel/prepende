"""Phase 1 durability smoke — goals survive a crash and can resume. Zero infra.

Simulates a crash (a run left 'running'), confirms a fresh session detects it as
interrupted, that a completed loop run is journaled 'done', and that resuming
re-runs the interrupted goal. Run from the repo root:
    python tests/smoke_phase1_durable.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kernel.core.runs import RunStore  # noqa: E402
from kernel.core.loop import GoalLoop  # noqa: E402
from kernel.core.strategist import RulesStrategist  # noqa: E402
from models.echo import EchoGateway  # noqa: E402
from workspace.local import LocalWorkspace  # noqa: E402


async def main() -> None:
    tmp = tempfile.mkdtemp(prefix="engram_dur_")
    db = os.path.join(tmp, "runs.db")

    # Session 1: a goal starts, then the process "crashes" (left 'running').
    rs1 = RunStore(db)
    rs1.start("goal_crashed_001", "research the Vermont market")
    del rs1

    # Session 2: a fresh store detects the interrupted goal.
    rs2 = RunStore(db)
    intr = rs2.interrupted()
    assert any(r["goal_id"] == "goal_crashed_001" for r in intr), f"interrupted not detected: {intr}"

    # A normal loop run is journaled as done.
    gw = EchoGateway()
    loop = GoalLoop(gw, RulesStrategist(gw), LocalWorkspace(os.path.join(tmp, "ws")), runs=rs2)
    await loop.run("a quick goal", lambda ev: asyncio.sleep(0))
    done = [r for r in rs2.recent(10) if r["status"] == "done"]
    assert done, "completed run not journaled"

    # Resume re-runs the interrupted goal's text.
    crashed = next(r for r in intr if r["goal_id"] == "goal_crashed_001")
    await loop.run(crashed["goal"], lambda ev: asyncio.sleep(0))
    rs2.finish("goal_crashed_001", "(resumed)")
    assert not any(r["goal_id"] == "goal_crashed_001" for r in rs2.interrupted()), "still flagged after resume"

    print("PHASE 1 DURABLE SMOKE: OK")
    print(f"  interrupted detected after crash : yes ({crashed['goal']!r})")
    print(f"  completed runs journaled         : {len(done)}")
    print("  resume cleared the interrupted flag: yes")


if __name__ == "__main__":
    asyncio.run(main())
