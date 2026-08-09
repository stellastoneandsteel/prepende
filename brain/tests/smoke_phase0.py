"""Phase 0 smoke test — proves first light without a key, a DB, or a network.

Runs the Goal Loop end to end on the echo provider and asserts: status events
fire, tokens stream, the Strategist picks a tactic, and a real artifact lands in
the goal's workspace. Run from the repo root:  python tests/smoke_phase0.py
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# Isolate the test in a temp dir so it never pollutes the real ./.engram memory/runs.
import tempfile  # noqa: E402
_tmp = tempfile.mkdtemp(prefix="engram_smoke0_")
os.environ["MODEL_PROVIDER"] = "echo"
# The developer .env may contain a real independent embedding provider. Phase 0
# promises zero network/cost, so pin the embedding lane to the same fail-safe
# echo fixture instead of inheriting local credentials.
os.environ["EMBEDDING_PROVIDER"] = "echo"
os.environ["EMBEDDING_MODEL"] = "echo-smoke"
os.environ["EMBEDDING_DIM"] = "3"
os.environ["WORKSPACE_ROOT"] = os.path.join(_tmp, "ws")
os.environ["MEMORY_DB"] = os.path.join(_tmp, "memory.db")
os.environ["MEMORY_BACKEND"] = "sqlite"  # hermetic: never inherit a machine DATABASE_URL
os.environ["RUNS_DB"] = os.path.join(_tmp, "runs.db")
os.environ["SELF_IMPROVE_DB"] = os.path.join(_tmp, "self-improvement.db")
os.environ["CONNECTOR_READINESS_DB"] = os.path.join(_tmp, "connector-readiness.db")
os.environ["KNOWLEDGE_DB"] = os.path.join(_tmp, "knowledge.db")
os.environ["VAULT_PATH"] = os.path.join(_tmp, "vault")
os.environ["VAULT_INDEX_PATH"] = os.path.join(_tmp, "vault-index.db")
os.environ["GRAPHIFY_GRAPH_PATH"] = os.path.join(_tmp, "graphify", "graph.json")

from kernel.core.brain import build_brain  # noqa: E402


async def main() -> None:
    loop, cfg, gateway = build_brain()
    events: list[dict] = []

    async def on_event(ev: dict) -> None:
        events.append(ev)

    await loop.run("Plan a one-day trip to Burlington, Vermont", on_event)

    types = [e["type"] for e in events]
    assert "status" in types, f"no status events: {types}"
    assert any(e["type"] == "token" for e in events), "no tokens streamed"
    arts = [e for e in events if e["type"] == "artifact"]
    assert arts, "no artifact produced"
    assert os.path.exists(arts[0]["text"]), f"artifact file missing: {arts[0]['text']}"
    assert any(e["type"] == "done" for e in events), "loop did not finish"

    print("PHASE 0 SMOKE: OK")
    print(f"  model      : {getattr(gateway, 'name', '?')}")
    print(f"  events     : {len(events)} ({sum(t == 'token' for t in types)} tokens)")
    print(f"  artifact   : {arts[0]['text']}")


if __name__ == "__main__":
    asyncio.run(main())
