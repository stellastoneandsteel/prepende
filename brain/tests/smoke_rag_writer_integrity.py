"""RAG writer integrity: no await-in-transaction, stable snapshots, empty files.

Hermetic: every vault and SQLite index lives under one temporary directory;
there are no provider or network calls.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from knowledge.rag import VaultRagIndex  # noqa: E402


def _vault(root: Path) -> Path:
    vault = root / "vault"
    (vault / "wiki").mkdir(parents=True)
    (vault / "raw").mkdir(parents=True)
    return vault


async def _write_transaction_is_not_held_during_embedding(root: Path) -> None:
    vault = _vault(root)
    (vault / "wiki" / "page.md").write_text(
        "# Page\n\nThe provider call must not hold a SQLite write lock.\n",
        encoding="utf-8",
    )
    database = root / "rag.db"
    index = VaultRagIndex(str(vault), index_path=str(database))

    async def embed(texts: list[str]) -> list[list[float]]:
        # This independent writer deterministically fails with `database is
        # locked` if rebuild() has already opened DELETE/INSERT transaction
        # and then awaits the embedding provider.
        with sqlite3.connect(database, timeout=0.2) as connection:
            connection.execute("PRAGMA busy_timeout=200")
            connection.execute(
                "INSERT INTO index_meta(key,value) VALUES('embed_probe','wrote') "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value"
            )
        await asyncio.sleep(0)
        return [[float(len(text)), 1.0] for text in texts]

    index.set_embedder(embed, profile="test:transaction:2:v1")
    await index.rebuild()
    with sqlite3.connect(database) as connection:
        probe = connection.execute(
            "SELECT value FROM index_meta WHERE key='embed_probe'"
        ).fetchone()
        journal = str(connection.execute("PRAGMA journal_mode").fetchone()[0]).lower()
    configured = index._conn()  # focused configuration assertion
    try:
        busy_timeout = int(configured.execute("PRAGMA busy_timeout").fetchone()[0])
    finally:
        configured.close()
    assert probe == ("wrote",), probe
    assert journal == "wal", journal
    assert busy_timeout >= 15_000, busy_timeout


async def _same_stat_edit_retries_on_hash_change(root: Path) -> None:
    vault = _vault(root)
    page = vault / "wiki" / "mutable.md"
    old = "# Mutable\n\nalpha signal\n"
    new = "# Mutable\n\nbravo signal\n"
    assert len(old.encode()) == len(new.encode())
    page.write_text(old, encoding="utf-8")
    database = root / "rag.db"
    index = VaultRagIndex(str(vault), index_path=str(database))
    await index.rebuild()
    original_stat = page.stat()
    calls = 0

    async def mutating_embed(texts: list[str]) -> list[list[float]]:
        nonlocal calls
        calls += 1
        if calls == 1:
            page.write_text(new, encoding="utf-8")
            # Preserve both size and mtime so only the content hash exposes
            # that the source changed while embeddings were in flight.
            os.utime(
                page,
                ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
            )
        await asyncio.sleep(0)
        return [[float(len(text)), 1.0] for text in texts]

    index.set_embedder(mutating_embed, profile="test:snapshot:2:v1")
    receipt = await index.refresh()
    status = index.status()
    with sqlite3.connect(database) as connection:
        stored_hash = connection.execute(
            "SELECT content_hash FROM source_files WHERE path='wiki/mutable.md'"
        ).fetchone()[0]
        indexed_content = connection.execute(
            "SELECT content FROM chunks WHERE path='wiki/mutable.md'"
        ).fetchone()[0]
    assert calls >= 2, calls
    assert receipt["reindexed"] == 1, receipt
    assert "bravo signal" in indexed_content and "alpha signal" not in indexed_content
    assert stored_hash == hashlib.sha256(new.encode()).hexdigest(), stored_hash
    assert status["stale"] is False and status["semantic_ready"] is True, status


async def _empty_sources_are_durably_accounted(root: Path) -> None:
    vault = _vault(root)
    (vault / "wiki" / "empty.md").write_text("", encoding="utf-8")
    (vault / "raw" / "note.md").write_text("# Note\n\nlexical fact\n", encoding="utf-8")
    database = root / "rag.db"
    index = VaultRagIndex(str(vault), index_path=str(database))
    await index.rebuild()
    status = index.status()
    assert status["source_files"] == status["indexed_files"] == 2, status
    assert status["chunks"] == 1 and status["stale"] is False, status
    unchanged = await index.refresh()
    assert unchanged["reindexed"] == 0, unchanged
    with sqlite3.connect(database) as connection:
        empty = connection.execute(
            "SELECT chunk_count FROM source_files WHERE path='wiki/empty.md'"
        ).fetchone()
    assert empty == (0,), empty


async def _legacy_index_is_adopted_without_vector_loss(root: Path) -> None:
    vault = _vault(root)
    page = vault / "wiki" / "legacy.md"
    page.write_text("# Legacy\n\nretained vector\n", encoding="utf-8")
    database = root / "rag.db"
    database.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE chunks (id TEXT PRIMARY KEY,path TEXT NOT NULL,"
            "page TEXT NOT NULL,section TEXT,content TEXT NOT NULL,"
            "mtime REAL NOT NULL,embedding TEXT)"
        )
        connection.execute(
            "CREATE TABLE index_meta (key TEXT PRIMARY KEY,value TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO chunks VALUES(?,?,?,?,?,?,?)",
            (
                "legacy_chunk", "wiki/legacy.md", "legacy", "Legacy",
                "# Legacy\n\nretained vector", page.stat().st_mtime, "[1.0,2.0]",
            ),
        )
    index = VaultRagIndex(str(vault), index_path=str(database))

    async def legacy_embed(texts: list[str]) -> list[list[float]]:
        return [[float(len(text)), 1.0] for text in texts]

    index.set_embedder(legacy_embed, expected_dimension=2)
    status = index.status()
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT id,embedding FROM chunks WHERE path='wiki/legacy.md'"
        ).fetchone()
    assert row == ("legacy_chunk", "[1.0,2.0]"), row
    assert status["indexed_files"] == 1 and status["stale"] is False, status
    assert status["embedded_chunks"] == 1, status


async def _upgraded_index_backfills_provenance_without_vector_loss(root: Path) -> None:
    vault = _vault(root)
    page = vault / "wiki" / "reviewed.md"
    page.write_text(
        "---\n"
        'knowledge_id: "approved-doc"\n'
        'approval_status: "source_policy_approved"\n'
        f'bundle_sha256: "{"a" * 64}"\n'
        "---\n\n"
        "# Reviewed\n\nprovenance backfill target\n",
        encoding="utf-8",
    )
    database = root / "rag.db"
    original = VaultRagIndex(str(vault), index_path=str(database))

    async def embed(texts: list[str]) -> list[list[float]]:
        return [[float(len(text)), 1.0] for text in texts]

    original.set_embedder(embed, profile="test:provenance-upgrade:2:v1")
    await original.rebuild()
    with sqlite3.connect(database) as connection:
        before = connection.execute(
            "SELECT id,embedding FROM chunks WHERE path='wiki/reviewed.md'"
        ).fetchone()
        connection.execute(
            "CREATE TABLE chunks_legacy (id TEXT PRIMARY KEY,path TEXT NOT NULL,"
            "page TEXT NOT NULL,section TEXT,content TEXT NOT NULL,"
            "mtime REAL NOT NULL,embedding TEXT)"
        )
        connection.execute(
            "INSERT INTO chunks_legacy "
            "SELECT id,path,page,section,content,mtime,embedding FROM chunks"
        )
        connection.execute("DROP TABLE chunks")
        connection.execute("ALTER TABLE chunks_legacy RENAME TO chunks")

    upgraded = VaultRagIndex(str(vault), index_path=str(database))
    hits = await upgraded.search("provenance backfill target")
    assert hits and hits[0]["sourceId"] == "approved-doc", hits
    assert hits[0]["approvalStatus"] == "source_policy_approved", hits[0]
    assert hits[0]["bundleSha256"] == "a" * 64, hits[0]
    with sqlite3.connect(database) as connection:
        after = connection.execute(
            "SELECT id,embedding,metadata FROM chunks WHERE path='wiki/reviewed.md'"
        ).fetchone()
    assert after[:2] == before, (before, after)
    assert '"sourceId":"approved-doc"' in after[2], after[2]


async def _deleted_legacy_source_is_stale_and_purged(root: Path) -> None:
    vault = _vault(root)
    database = root / "rag.db"
    database.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE chunks (id TEXT PRIMARY KEY,path TEXT NOT NULL,"
            "page TEXT NOT NULL,section TEXT,content TEXT NOT NULL,"
            "mtime REAL NOT NULL,embedding TEXT)"
        )
        connection.execute(
            "CREATE TABLE index_meta (key TEXT PRIMARY KEY,value TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO chunks VALUES(?,?,?,?,?,?,?)",
            (
                "deleted_chunk", "wiki/deleted.md", "deleted", "Deleted",
                "deleted-source-secret", 1.0, None,
            ),
        )
    index = VaultRagIndex(str(vault), index_path=str(database))
    stale = index.status()
    assert stale["stale"] is True and stale["orphan_chunk_paths"] == 1, stale
    assert stale["lexical_ready"] is False, stale
    receipt = await index.refresh()
    clean = index.status()
    assert receipt["files"] == 0, receipt
    assert clean["stale"] is False and clean["chunks"] == 0, clean
    assert await index.search("deleted-source-secret") == []


async def _profile_mismatch_fails_closed_to_lexical(root: Path) -> None:
    vault = _vault(root)
    (vault / "wiki" / "lexical.md").write_text(
        "# Lexical target\n\nlexical-target exact phrase\n", encoding="utf-8"
    )
    (vault / "wiki" / "decoy.md").write_text(
        "# Decoy\n\nunrelated material\n", encoding="utf-8"
    )
    database = root / "rag.db"
    first = VaultRagIndex(str(vault), index_path=str(database))
    first_calls = 0

    async def first_embed(texts: list[str]) -> list[list[float]]:
        nonlocal first_calls
        first_calls += 1
        return [[0.0, 1.0] for _ in texts]

    async def second_embed(texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]

    first.set_embedder(
        first_embed, profile="test:profile-a:2:v1", expected_dimension=2
    )
    await first.rebuild()
    second = VaultRagIndex(str(vault), index_path=str(database))
    second.set_embedder(
        second_embed, profile="test:profile-b:2:v1", expected_dimension=2
    )
    assert (await second.backfill_all(max_rounds=3))["complete"] is True

    mismatch = first.status()
    assert mismatch["embedding_profile_mismatch"] is True, mismatch
    assert mismatch["semantic_ready"] is False, mismatch
    blocked = await first.backfill_all(max_rounds=3)
    assert blocked["complete"] is False, blocked
    assert blocked["reason"] == "embedding_profile_mismatch", blocked

    calls_before_search = first_calls
    connections = 0
    original_conn = first._conn

    def counted_conn():
        nonlocal connections
        connections += 1
        return original_conn()

    first._conn = counted_conn  # type: ignore[method-assign]
    hits = await first.search("lexical-target exact")
    assert connections == 1, connections
    assert first_calls == calls_before_search, (first_calls, calls_before_search)
    assert hits and hits[0]["page"] == "lexical", hits


async def _mixed_and_nonfinite_vectors_remain_null(root: Path) -> None:
    vault = _vault(root)
    (vault / "wiki" / "dimensions.md").write_text(
        "# First\n\nvalid three dimensional vector\n\n"
        "## Second\n\nwrong two dimensional vector\n\n"
        "## Third\n\nnonfinite vector\n",
        encoding="utf-8",
    )
    database = root / "rag.db"
    index = VaultRagIndex(str(vault), index_path=str(database))

    async def mixed_embed(texts: list[str]) -> list[list[float]]:
        vectors = ([1.0, 2.0, 3.0], [1.0, 2.0], [float("nan"), 2.0, 3.0])
        return [list(vectors[position]) for position, _ in enumerate(texts)]

    index.set_embedder(
        mixed_embed, profile="test:mixed:3:v1", expected_dimension=3
    )
    await index.rebuild()
    status = index.status()
    with sqlite3.connect(database) as connection:
        stored = connection.execute(
            "SELECT embedding FROM chunks ORDER BY section"
        ).fetchall()
    assert sum(value[0] is not None for value in stored) == 1, stored
    assert status["stored_dimensions"] == [3], status
    assert status["embedded_chunks"] == 1 and status["missing_embeddings"] == 2, status
    assert status["semantic_ready"] is False, status
    # Status scans every stored vector rather than trusting one sample. A
    # legacy/corrupt mixed-dimensional row is therefore visible and cannot be
    # certified as one semantic space.
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE chunks SET embedding='[9.0,9.0]' "
            "WHERE embedding IS NULL AND id=(SELECT id FROM chunks "
            "WHERE embedding IS NULL LIMIT 1)"
        )
    mixed_status = index.status()
    assert mixed_status["stored_dimensions"] == [2, 3], mixed_status
    assert mixed_status["actual_dimension"] is None, mixed_status
    assert mixed_status["embedding_dimension_mismatch"] is True, mixed_status
    assert mixed_status["semantic_ready"] is False, mixed_status


async def _same_path_writers_are_serialized(root: Path) -> None:
    vault = _vault(root)
    (vault / "wiki" / "concurrent.md").write_text(
        "# Concurrent\n\nOnly one writer prepares this index at a time.\n",
        encoding="utf-8",
    )
    database = root / "rag.db"
    first = VaultRagIndex(str(vault), index_path=str(database))
    second = VaultRagIndex(str(vault), index_path=str(database))
    active = 0
    peak = 0

    async def slow_embed(texts: list[str]) -> list[list[float]]:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.02)
        active -= 1
        return [[float(len(text)), 1.0] for text in texts]

    first.set_embedder(slow_embed, profile="test:serialized:2:v1")
    second.set_embedder(slow_embed, profile="test:serialized:2:v1")
    results = await asyncio.gather(first.rebuild(), second.rebuild())
    assert peak == 1, peak
    assert all(result["files"] == 1 and result["chunks"] == 1 for result in results), results
    status = first.status()
    assert status["stale"] is False and status["embedded_chunks"] == 1, status


async def _tracked_source_change_blocks_until_rebuilt(root: Path) -> None:
    vault = _vault(root)
    page = vault / "wiki" / "tracked.md"
    page.write_text("# Tracked\n\noriginal reviewed knowledge\n", encoding="utf-8")
    subprocess.run(("git", "init", "-q", str(root)), check=True)
    subprocess.run(("git", "-C", str(root), "add", "vault/wiki/tracked.md"), check=True)
    index = VaultRagIndex(str(vault), index_path=str(root / "rag.db"))
    await index.rebuild()
    ready = index.status()
    assert ready["lexical_ready"] is True and ready["stale"] is False, ready

    page.write_text("# Tracked\n\nchanged reviewed knowledge\n", encoding="utf-8")
    changed = index.status()
    assert changed["stale"] is True and changed["lexical_ready"] is False, changed

    await index.rebuild()
    restored = index.status()
    assert restored["stale"] is False and restored["lexical_ready"] is True, restored
    assert restored["source_files"] == restored["indexed_files"] == 1, restored


async def main() -> None:
    root = Path(tempfile.mkdtemp(prefix="prepende_rag_integrity_"))
    await _write_transaction_is_not_held_during_embedding(root / "transaction")
    await _same_stat_edit_retries_on_hash_change(root / "snapshot")
    await _empty_sources_are_durably_accounted(root / "empty")
    await _legacy_index_is_adopted_without_vector_loss(root / "legacy")
    await _upgraded_index_backfills_provenance_without_vector_loss(
        root / "provenance-upgrade"
    )
    await _deleted_legacy_source_is_stale_and_purged(root / "legacy-deleted")
    await _profile_mismatch_fails_closed_to_lexical(root / "profile-mismatch")
    await _mixed_and_nonfinite_vectors_remain_null(root / "dimensions")
    await _same_path_writers_are_serialized(root / "concurrent")
    await _tracked_source_change_blocks_until_rebuilt(root / "tracked-change")
    print("RAG WRITER INTEGRITY SMOKE: OK")
    print("  embedding await: outside SQLite write transaction")
    print("  source snapshot: stat + size + SHA-256, retry on mutation")
    print("  empty markdown: indexed in durable source metadata")
    print("  migration: legacy vectors adopted without rebuilding the live index")
    print("  provenance migration: upgraded chunks backfilled from the same source snapshot")
    print("  legacy deletion: orphan chunks stale immediately and purge on refresh")
    print("  vector space: profile mismatch degrades to one-snapshot lexical search")
    print("  dimensions: mixed/nonfinite vectors remain NULL; semantic not ready")
    print("  concurrency: one writer per resolved index path, WAL + busy timeout")
    print("  tracked source change: stale until a complete lexical rebuild")


if __name__ == "__main__":
    asyncio.run(main())
