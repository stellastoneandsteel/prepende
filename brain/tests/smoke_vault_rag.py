"""Smoke: the vault RAG projection — disposable, rebuildable, hybrid, fail-safe.

Proves (temp vault, zero infra):
  1. rebuild() indexes wiki + raw pages into chunks
  2. lexical search finds the right page with NO embedder (fail-safe path)
  3. a fake embedder engages the vector path without breaking ranking
  4. refresh() picks up edited/new files and drops deleted ones
  5. deleting the index db and rebuilding restores search (disposability)
  6. VaultKnowledge.search() serves the same results through the kernel port

Run: MODEL_PROVIDER=echo python3 tests/smoke_vault_rag.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from knowledge.rag import VaultRagIndex  # noqa: E402
from knowledge.vault import VaultKnowledge  # noqa: E402

PAGE_GRANITE = """---
type: entity
status: stable
---

# Granite supplier

The primary granite supplier ships from Barre on Thursdays.

## Pricing

Slab pricing is quoted per job; never recall a price from memory.
"""

PAGE_KILN = """---
type: concept
status: draft
---

# Kiln schedule

The kiln runs Monday and Wednesday. Firings need 14 hours of cooldown.
"""


async def main() -> None:
    tmp = tempfile.mkdtemp(prefix="engram_rag_")
    os.environ["MEMORY_DB"] = os.path.join(tmp, "store", "memory.db")
    os.environ["MEMORY_BACKEND"] = "sqlite"  # hermetic: never inherit a machine DATABASE_URL
    vault = os.path.join(tmp, "vault")
    os.makedirs(os.path.join(vault, "wiki"))
    os.makedirs(os.path.join(vault, "raw"))
    with open(os.path.join(vault, "wiki", "granite-supplier.md"), "w") as f:
        f.write(PAGE_GRANITE)
    with open(os.path.join(vault, "raw", "kiln-notes.md"), "w") as f:
        f.write(PAGE_KILN)

    index_path = os.path.join(tmp, "store", "vault_index.db")
    idx = VaultRagIndex(vault, index_path=index_path)

    # 1. Rebuild indexes both folders.
    stats = await idx.rebuild()
    assert stats["files"] == 2 and stats["chunks"] >= 3, stats
    print(f"OK rebuild: {stats}")

    identity = idx.retrieval_identity()
    assert set(identity) == {
        "corpusRootHash", "indexPathHash", "indexRevision", "sourceFiles", "chunks",
    }, identity
    assert identity["sourceFiles"] == 2 and identity["chunks"] == stats["chunks"], identity
    assert all(
        str(identity[key]).startswith("sha256:")
        for key in ("corpusRootHash", "indexPathHash", "indexRevision")
    ), identity
    assert idx.retrieval_identity() == identity
    initial_revision = identity["indexRevision"]
    print("OK retrieval identity: physical corpus + deterministic index revision")

    # 2. Lexical search, no embedder (the fail-safe default).
    hits, bound_identity = await idx.search_with_identity("granite supplier Barre")
    assert hits and hits[0]["page"] == "granite-supplier", hits
    assert hits[0]["score"] > 0, hits
    assert bound_identity == identity
    print("OK lexical search: right page first, no embedder needed")

    # Real SQLite searches must enter independent worker threads. A barrier
    # proves overlap without asserting that either query finishes within a
    # machine-dependent duration; each worker performs the original DB read
    # after both have entered.
    original_snapshot = idx._read_search_snapshot
    concurrent_gate = threading.Barrier(2)

    def synchronized_snapshot():
        concurrent_gate.wait(timeout=5)
        return original_snapshot()

    idx._read_search_snapshot = synchronized_snapshot
    try:
        granite_hits, kiln_hits = await asyncio.wait_for(
            asyncio.gather(
                idx.search("granite supplier Barre"),
                idx.search("kiln cooldown hours"),
            ),
            timeout=10,
        )
    finally:
        idx._read_search_snapshot = original_snapshot
    assert granite_hits[0]["page"] == "granite-supplier", granite_hits
    assert kiln_hits[0]["page"] == "kiln-notes", kiln_hits
    print("OK async search: two real SQLite snapshots overlap on separate workers")

    # Once a worker has definitely entered a blocked read, wait_for must still
    # be able to cancel the asyncio task. Events control the block/release; the
    # timeout is only the cancellation trigger, not a performance assertion.
    entered = threading.Event()
    release = threading.Event()
    worker_finished = threading.Event()

    def blocked_snapshot():
        entered.set()
        try:
            if not release.wait(timeout=5):
                raise RuntimeError("test did not release blocked RAG worker")
            return original_snapshot()
        finally:
            worker_finished.set()

    idx._read_search_snapshot = blocked_snapshot
    timed_search = asyncio.create_task(idx.search("granite timeout proof"))
    try:
        assert await asyncio.to_thread(entered.wait, 5), "RAG worker never entered"
        try:
            await asyncio.wait_for(timed_search, timeout=0.1)
        except asyncio.TimeoutError:
            pass
        else:
            raise AssertionError("blocked prepared search ignored asyncio timeout")
    finally:
        release.set()
        assert await asyncio.to_thread(worker_finished.wait, 5), "RAG worker did not exit"
        idx._read_search_snapshot = original_snapshot
    original_score = idx._score_search_snapshot
    score_entered = threading.Event()
    release_score = threading.Event()
    score_finished = threading.Event()

    def blocked_score(snapshot, *, terms, qvec, k):
        score_entered.set()
        try:
            if not release_score.wait(timeout=5):
                raise RuntimeError("test did not release blocked RAG scorer")
            return original_score(snapshot, terms=terms, qvec=qvec, k=k)
        finally:
            score_finished.set()

    idx._score_search_snapshot = blocked_score
    timed_score = asyncio.create_task(idx.search("granite timeout proof"))
    try:
        assert await asyncio.to_thread(score_entered.wait, 5), "RAG scorer never entered"
        try:
            await asyncio.wait_for(timed_score, timeout=0.1)
        except asyncio.TimeoutError:
            pass
        else:
            raise AssertionError("blocked prepared scoring ignored asyncio timeout")
    finally:
        release_score.set()
        assert await asyncio.to_thread(score_finished.wait, 5), "RAG scorer did not exit"
        idx._score_search_snapshot = original_score
    print("OK async timeout: wait_for cancels blocked SQLite reads and scoring workers")

    # A refresh may commit after a search has captured its read transaction.
    # Pause exactly after that capture, refresh the real backend, then prove the
    # returned identity still names the old rows used for ranking rather than
    # the new current revision.
    snapshot_vault = os.path.join(tmp, "snapshot-vault")
    os.makedirs(os.path.join(snapshot_vault, "wiki"))
    os.makedirs(os.path.join(snapshot_vault, "raw"))
    snapshot_page = os.path.join(snapshot_vault, "wiki", "snapshot.md")
    with open(snapshot_page, "w") as f:
        f.write("# Snapshot\n\nThe immutable marker is old-snapshot.\n")
    snapshot_idx = VaultRagIndex(
        snapshot_vault,
        index_path=os.path.join(tmp, "store", "snapshot-index.db"),
    )
    await snapshot_idx.rebuild()
    old_identity = snapshot_idx.retrieval_identity()
    captured = threading.Event()
    finish_capture = threading.Event()
    original_capture = snapshot_idx._read_search_snapshot

    def paused_snapshot():
        snapshot = original_capture()
        captured.set()
        if not finish_capture.wait(timeout=5):
            raise RuntimeError("test did not release captured RAG snapshot")
        return snapshot

    snapshot_idx._read_search_snapshot = paused_snapshot
    old_search = asyncio.create_task(
        snapshot_idx.search_with_identity("immutable old snapshot")
    )
    try:
        assert await asyncio.to_thread(captured.wait, 5), "snapshot was not captured"
        with open(snapshot_page, "w") as f:
            f.write("# Snapshot\n\nThe immutable marker is new-snapshot.\n")
        await snapshot_idx.refresh()
        new_identity = original_capture().identity
        assert new_identity["indexRevision"] != old_identity["indexRevision"]
    finally:
        finish_capture.set()
    old_hits, search_identity = await old_search
    snapshot_idx._read_search_snapshot = original_capture
    assert search_identity == old_identity, (search_identity, old_identity)
    assert old_hits and "old-snapshot" in old_hits[0]["content"], old_hits
    print("OK atomic identity: concurrent refresh cannot relabel captured search rows")

    # 3. Vector path with a deterministic fake embedder.
    async def fake_embed(texts):
        def v(t):
            t = t.lower()
            return [float(t.count("kiln")), float(t.count("granite")), 1.0]
        return [v(t) for t in texts]

    idx.set_embedder(fake_embed, profile="fake:space-a:3:v1")
    await idx.rebuild()
    ready = idx.status()
    assert ready["lexical_ready"] and ready["semantic_ready"], ready
    assert ready["embedded_chunks"] == ready["chunks"], ready
    hits = await idx.search("kiln cooldown hours")
    assert hits and hits[0]["page"] == "kiln-notes", hits
    print("OK hybrid search: vector path ranks the kiln page first")
    print("OK RAG readiness: indexed + embedded counts are introspectable")

    # Same-dimensional model changes still invalidate incompatible vectors.
    async def fake_embed_b(texts):
        def v(t):
            t = t.lower()
            return [float(t.count("granite")), float(t.count("kiln")), 1.0]
        return [v(t) for t in texts]

    change = idx.set_embedder(fake_embed_b, profile="fake:space-b:3:v1")
    assert change["changed"] is True and change["invalidated"] == stats["chunks"], change
    profile_refresh = await idx.refresh()
    assert profile_refresh["reindexed"] == 0, profile_refresh
    assert profile_refresh["backfilled"] == stats["chunks"], profile_refresh
    hits = await idx.search("kiln cooldown hours")
    assert hits and hits[0]["page"] == "kiln-notes", hits
    idx_same = VaultRagIndex(vault, index_path=index_path)
    unchanged = idx_same.set_embedder(fake_embed_b, profile="fake:space-b:3:v1")
    assert unchanged == {"changed": False, "invalidated": 0}, unchanged
    print("OK embedding profile: model-space switch invalidates + backfills vectors")

    # 4. refresh() tracks the markdown.
    with open(os.path.join(vault, "wiki", "veneer-rack.md"), "w") as f:
        f.write("# Veneer rack\n\nThe veneer rack restocks from the Bristol yard.\n")
    os.remove(os.path.join(vault, "raw", "kiln-notes.md"))
    stats = await idx.refresh()
    assert stats["files"] == 2, stats
    assert (await idx.search("veneer rack Bristol"))[0]["page"] == "veneer-rack"
    assert all(h["page"] != "kiln-notes" for h in await idx.search("kiln cooldown")), \
        "deleted page still searchable"
    changed_identity = idx.retrieval_identity()
    assert changed_identity["corpusRootHash"] == identity["corpusRootHash"]
    assert changed_identity["indexPathHash"] == identity["indexPathHash"]
    assert changed_identity["indexRevision"] != initial_revision
    print("OK refresh: new page indexed, deleted page gone")

    # 5. Disposability: kill the db, rebuild from markdown, search restored.
    os.remove(index_path)
    idx2 = VaultRagIndex(vault, index_path=index_path)
    await idx2.rebuild()
    assert (await idx2.search("granite supplier"))[0]["page"] == "granite-supplier"
    print("OK disposable: index deleted and fully restored from the vault")

    # 6. Through the kernel port.
    vk = VaultKnowledge(vault)
    hits = await vk.search("granite Barre Thursdays")
    assert hits and hits[0]["page"] == "granite-supplier", hits
    assert set(await vk.retrieval_identity()) == set(identity)
    print("OK VaultKnowledge.search: kernel port serves the projection")

    print("\nVAULT RAG SMOKE: OK — the brain reads markdown, Obsidian is just a viewer")


if __name__ == "__main__":
    asyncio.run(main())
