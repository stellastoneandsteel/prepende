"""Smoke: unified recall — memory + vault RAG + one-hop graph neighbors.

Proves (temp vault, fakes only, no network):
  1. unified_recall merges scoped memory with vault RAG hits, source-labeled
  2. the one-hop wikilink walk recalls a neighbor page that shares ZERO terms
     with the query — the associative read keyword/vector matching can't do
  3. vault recall is OFF by default (vault=False -> memory only): a
     tenant-scoped loop never reads the shared vault
  4. the neighbor budget holds — a hub with many links can't flood recall
  5. a raising vault degrades recall to memory-only (fail-safe, run completes)
  6. independent memory/vault reads start concurrently
  7. deterministic fusion rejects malformed/duplicate rows and holds a global
     recall budget while preserving direct and associative source diversity
  8. a full GoalLoop run with vault_recall=True reports recall sources in the
     receipt; the default loop's receipt carries no vault reads

Run: MODEL_PROVIDER=echo python3 tests/smoke_recall_graph.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kernel.core.loop import GoalLoop  # noqa: E402
from kernel.core.recall import unified_recall  # noqa: E402
from kernel.core.strategist import RulesStrategist  # noqa: E402
from workspace.local import LocalWorkspace  # noqa: E402

PAGE_RUNBOOK = """---
type: synthesis
---

# Launch runbook

The alpha launch checklist gates every public release.
Pricing context lives at [[pricing-history]].
"""

# Deliberately shares NO terms with the query "alpha launch checklist" —
# reachable only through the wikilink from the runbook page.
PAGE_PRICING = """---
type: synthesis
---

# Pricing history

Grandfathered pilots keep their original rate; the 2025 cohort was repriced twice.
"""

PAGE_NOISE = """---
type: synthesis
---

# Kiln maintenance

The kiln vents get cleaned quarterly. Unrelated to releases.
"""

QUERY = "alpha launch checklist"


class FakeMemory:
    async def search(self, q, scope="default", k=5):
        return [{"content": "owner prefers staged rollouts", "kind": "semantic"}]


class RaisingKnowledge:
    async def search(self, query, k=8):
        raise RuntimeError("vault index corrupted")


class FloodMemory:
    async def search(self, q, scope="default", k=5):
        return [
            {"content": f"operational memory {n}", "kind": "semantic"}
            for n in range(8)
        ]


class NoisyMemory:
    async def search(self, q, scope="default", k=5):
        return [
            {"content": "same approved fact"},
            {"content": " same   approved FACT "},
            {"content": ""},
            {"kind": "semantic"},
            {"content": "second approved fact"},
            {"content": "third approved fact"},
            {"content": "fourth approved fact"},
        ]


class FakeGateway:
    name = "fake"

    async def complete(self, messages, max_tokens=0, **kw) -> str:
        return "answer"

    async def stream(self, messages, system=None, **kw):
        yield "answer"


def _write(vault: str, name: str, text: str) -> None:
    with open(os.path.join(vault, "wiki", f"{name}.md"), "w") as f:
        f.write(text)


async def main() -> None:
    tmp = tempfile.mkdtemp(prefix="engram_recall_")
    os.environ["MEMORY_DB"] = os.path.join(tmp, "store", "memory.db")
    os.environ["MEMORY_BACKEND"] = "sqlite"  # hermetic: never inherit a machine DATABASE_URL
    vault = os.path.join(tmp, "vault")
    os.makedirs(os.path.join(vault, "wiki"))
    _write(vault, "launch-runbook", PAGE_RUNBOOK)
    _write(vault, "pricing-history", PAGE_PRICING)
    _write(vault, "kiln-maintenance", PAGE_NOISE)

    from knowledge.vault import VaultKnowledge
    vk = VaultKnowledge(vault)  # no gateway, no embedder: pure lexical + graph

    # Sanity for claim 2: the neighbor page really shares no query terms.
    assert not any(t in PAGE_PRICING.lower() for t in QUERY.lower().split()), \
        "test setup broken: neighbor page overlaps the query"

    # 1 + 2. Unified recall merges memory + vault hits + the graph neighbor.
    rec = await unified_recall(QUERY, memory=FakeMemory(), knowledge=vk, vault=True)
    src = rec["sources"]
    assert src["memory"] == 1, src
    assert src["vault"] >= 1, src
    assert src["graphNeighbors"] >= 1, src
    by_source = {i.get("source") for i in rec["items"] if isinstance(i, dict)}
    assert "vault" in by_source and "vault_graph" in by_source, by_source
    neighbor = [i for i in rec["items"] if i.get("source") == "vault_graph"]
    assert any(i.get("page") == "pricing-history" for i in neighbor), neighbor
    assert any("Grandfathered pilots" in i["content"] for i in neighbor), neighbor
    assert all("kiln-maintenance" != i.get("page") for i in rec["items"]), \
        "unlinked noise page recalled"
    assert all("content" in i for i in rec["items"]), "tactic contract broken"
    print("OK associative read: graph neighbor recalled with zero term overlap")

    # 3. Default is memory-only — the shared vault stays unread.
    rec = await unified_recall(QUERY, memory=FakeMemory(), knowledge=vk)
    assert rec["sources"] == {"memory": 1, "vault": 0, "graphNeighbors": 0}, rec["sources"]
    assert all(i.get("source") is None for i in rec["items"]), rec["items"]
    print("OK tenant safety: vault recall requires explicit opt-in")

    # 4. Neighbor budget: a hub linking to many pages can't flood recall.
    links = " ".join(f"[[spoke-{n}]]" for n in range(12))
    _write(vault, "launch-runbook", PAGE_RUNBOOK.replace("[[pricing-history]]",
                                                         f"[[pricing-history]] {links}"))
    for n in range(12):
        _write(vault, f"spoke-{n}", f"# Spoke {n}\n\nFiller page {n}.\n")
    rec = await unified_recall(QUERY, memory=FakeMemory(), knowledge=vk, vault=True)
    assert rec["sources"]["graphNeighbors"] <= 2, rec["sources"]
    print(f"OK neighbor budget: 13 links -> {rec['sources']['graphNeighbors']} neighbor(s) recalled")

    # 5. A broken vault degrades to memory-only; nothing raises.
    rec = await unified_recall(QUERY, memory=FakeMemory(), knowledge=RaisingKnowledge(), vault=True)
    assert rec["sources"] == {"memory": 1, "vault": 0, "graphNeighbors": 0}, rec["sources"]
    print("OK fail-safe: raising vault -> memory-only recall")

    # 6. Memory and vault have no data dependency, so both reads start before
    # either is allowed to complete. A sequential implementation deadlocks and
    # trips the timeout.
    memory_started = asyncio.Event()
    vault_started = asyncio.Event()

    class CoordinatedMemory:
        async def search(self, q, scope="default", k=5):
            memory_started.set()
            await vault_started.wait()
            return [{"content": "concurrent memory"}]

    class CoordinatedKnowledge:
        async def search(self, query, k=8):
            vault_started.set()
            await memory_started.wait()
            return []

    concurrent = await asyncio.wait_for(
        unified_recall(
            QUERY, memory=CoordinatedMemory(), knowledge=CoordinatedKnowledge(), vault=True,
        ),
        timeout=1,
    )
    assert concurrent["sources"]["memory"] == 1, concurrent
    print("OK recall graph: independent memory and vault reads run concurrently")

    # 7a. Node contracts and the global budget are enforced before prompt
    # injection. Exact duplicates are deterministic code, not a model task.
    fused = await unified_recall(QUERY, memory=NoisyMemory(), max_items=3)
    selection = fused["selection"]
    assert len(fused["items"]) == 3, fused
    assert selection["retrieved"] == 7, selection
    assert selection["duplicatesDropped"] == 1, selection
    assert selection["invalidDropped"] == 2, selection
    assert selection["budgetDropped"] == 1, selection
    assert fused["sources"] == {"memory": 3, "vault": 0, "graphNeighbors": 0}, fused
    print("OK recall edge: malformed/duplicate rows dropped and global budget held")

    # 7b. A full memory bucket cannot starve direct reviewed knowledge or its
    # associative wikilink neighbor under the deterministic schedule.
    diverse = await unified_recall(
        QUERY, memory=FloodMemory(), knowledge=vk, vault=True, max_items=5,
    )
    assert len(diverse["items"]) == 5, diverse
    assert diverse["sources"]["memory"] >= 1, diverse
    assert diverse["sources"]["vault"] >= 1, diverse
    assert diverse["sources"]["graphNeighbors"] >= 1, diverse
    assert diverse["selection"]["budgetDropped"] >= 1, diverse["selection"]
    print("OK recall edge: source diversity survives a full memory bucket")

    # 8. Through the loop: receipt reports where recall read from.
    with tempfile.TemporaryDirectory() as ws_tmp:
        ws = LocalWorkspace(ws_tmp)
        gw = FakeGateway()
        events: list = []

        async def on_event(ev):
            events.append(ev)

        loop = GoalLoop(gw, RulesStrategist(gw), ws, memory=FakeMemory(),
                        knowledge=vk, vault_recall=True)
        receipt = await loop.run(QUERY, on_event)
        srcs = receipt.get("recall", {}).get("sources", {})
        assert srcs.get("memory") == 1 and srcs.get("vault", 0) >= 1, receipt.get("recall")
        assert receipt["memory"]["recalled"] == 1 + srcs["vault"] + srcs["graphNeighbors"], receipt["memory"]
        assert receipt["recall"]["selection"]["returned"] == receipt["memory"]["recalled"], receipt

        loop = GoalLoop(gw, RulesStrategist(gw), ws, memory=FakeMemory(), knowledge=vk)
        receipt = await loop.run(QUERY, on_event)
        srcs = receipt.get("recall", {}).get("sources", {})
        assert srcs.get("vault", 0) == 0 and srcs.get("graphNeighbors", 0) == 0, receipt.get("recall")
        print("OK receipts: vault_recall loop reports sources; default loop reads no vault")

    print("\nsmoke_recall_graph: ALL OK — the wiki graph is recall, not just browsing")


if __name__ == "__main__":
    asyncio.run(main())
