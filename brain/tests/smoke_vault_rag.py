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

    # 2. Lexical search, no embedder (the fail-safe default).
    hits = await idx.search("granite supplier Barre")
    assert hits and hits[0]["page"] == "granite-supplier", hits
    assert hits[0]["score"] > 0, hits
    print("OK lexical search: right page first, no embedder needed")

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
    print("OK VaultKnowledge.search: kernel port serves the projection")

    print("\nVAULT RAG SMOKE: OK — the brain reads markdown, Obsidian is just a viewer")


if __name__ == "__main__":
    asyncio.run(main())
