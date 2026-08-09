"""Phase 1 smoke test — proves the brain REMEMBERS across sessions.

Writes a fact via one store instance, then recalls it via a brand-new instance
(simulating a separate session), and confirms the Goal Loop writes a memory it
can later recall. Zero infra (stdlib sqlite). Run from the repo root:
    python tests/smoke_phase1.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memory.sqlite_store import SqliteMemoryStore  # noqa: E402
from kernel.core.config import Config  # noqa: E402
from kernel.core.loop import GoalLoop  # noqa: E402
from kernel.core.strategist import RulesStrategist  # noqa: E402
from models.echo import EchoGateway  # noqa: E402
from workspace.local import LocalWorkspace  # noqa: E402


async def main() -> None:
    tmp = tempfile.mkdtemp(prefix="engram_phase1_")
    db = os.path.join(tmp, "memory.db")

    # Session 1: write a fact, then close (drop the store).
    store1 = SqliteMemoryStore(db)
    await store1.write("The user's home base is Burlington, Vermont.", scope="default")
    del store1

    # Session 2: a brand-new store at the same path recalls it.
    store2 = SqliteMemoryStore(db)
    hits = list(await store2.search("where is the user based", scope="default", k=5))
    assert any("Burlington" in h["content"] for h in hits), f"did not recall across sessions: {hits}"

    # The Goal Loop writes a memory it can recall — ONLY because this dev/TUI
    # surface opts into memory_policy="auto" explicitly. The default ("candidate")
    # stages a non-durable Assess candidate instead (tests/smoke_memory_assess_gate.py).
    gw = EchoGateway()
    loop = GoalLoop(gw, RulesStrategist(gw), LocalWorkspace(os.path.join(tmp, "ws")), memory=store2, scope="default", memory_policy="auto")
    events: list[dict] = []
    await loop.run("Suggest a coffee shop near home", lambda ev: events.append(ev) or asyncio.sleep(0))
    after = list(await store2.search("coffee shop", scope="default", k=5))
    assert any("coffee" in h["content"].lower() for h in after), "loop did not persist a memory of the goal"

    print("PHASE 1 SMOKE: OK")
    print(f"  recall across sessions : yes ({len(hits)} hit(s); Burlington remembered)")
    print(f"  loop persisted goal    : yes ({len(after)} hit(s))")
    print(f"  db                     : {db}")


if __name__ == "__main__":
    asyncio.run(main())
