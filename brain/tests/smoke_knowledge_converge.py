"""Full knowledge backfill converges and reports provider failure honestly."""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from knowledge.rag import VaultRagIndex  # noqa: E402


async def main() -> None:
    tmp = tempfile.mkdtemp(prefix="prepende_knowledge_converge_")
    vault = os.path.join(tmp, "vault")
    wiki = os.path.join(vault, "wiki")
    os.makedirs(wiki)
    for index in range(70):
        with open(os.path.join(wiki, f"page-{index:02}.md"), "w", encoding="utf-8") as handle:
            handle.write(f"# Page {index}\n\nunique-token-{index:02} knowledge projection.\n")

    idx = VaultRagIndex(vault, index_path=os.path.join(tmp, "rag.db"))
    lexical = await idx.rebuild()
    assert lexical["chunks"] == 70 and lexical["missing"] == 70, lexical
    assert idx.status()["lexical_ready"] is True

    async def embed(texts):
        return [[float(len(text)), float("unique" in text), 1.0] for text in texts]

    idx.set_embedder(embed, profile="test:converge:3:v1")
    receipt = await idx.backfill_all(max_rounds=5)
    assert receipt["complete"] is True, receipt
    ready = idx.status()
    assert ready["embedded_chunks"] == ready["chunks"] == 70, ready
    assert ready["semantic_ready"] is True and ready["actual_dimension"] == 3, ready

    # A newly added source makes the projection visibly stale until convergence.
    with open(os.path.join(wiki, "new-page.md"), "w", encoding="utf-8") as handle:
        handle.write("# New Page\n\nnewly-added-source-token\n")
    assert idx.status()["stale"] is True
    updated = await idx.backfill_all(max_rounds=5)
    assert updated["complete"] is True, updated
    assert idx.status()["source_files"] == idx.status()["indexed_files"] == 71

    # Provider failure must terminate with a receipt instead of an infinite loop.
    async def unavailable(_texts):
        raise RuntimeError("offline")

    idx.set_embedder(unavailable, profile="test:offline:3:v1")
    failed = await idx.backfill_all(max_rounds=5)
    assert failed["complete"] is False, failed
    assert failed["reason"] == "embedding_provider_made_no_progress", failed
    assert failed["rounds"] <= 2, failed

    print("KNOWLEDGE CONVERGENCE SMOKE: OK")
    print("  70+ chunks: fully embedded")
    print("  new source: detected and indexed")
    print("  failed provider: bounded truthful receipt")


if __name__ == "__main__":
    asyncio.run(main())
