"""SqliteMemoryStore — persistent shared memory on stdlib sqlite3.

Zero infra, survives across sessions, accumulates. This is the Phase 1 memory
that makes Engram a brain and not a goldfish: a fact written in one session is
recalled in the next.

Recall is HYBRID: keyword overlap + vector cosine (when an embedder is wired
via `set_embedder`) + recency. Any embedder failure degrades recall to
lexical — never crashes, never fakes similarity. Rows are typed
(episodic | semantic | procedural) and facts are superseded, not overwritten
(`supersede`), so "what did we know when" stays answerable.

Production swap (same MemoryStore interface): Postgres + pgvector (semantic
recall) + Apache AGE (graph) + RLS (the shared-but-scoped boundary). RLS is a
Postgres concept; sqlite is single-user/local. See
docs/PREPENDE_MEMORY_ARCHITECTURE.md.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Sequence

from kernel.contracts import MemoryStore
from memory._scoring import (
    blend as _blend,
    cosine as _cosine,
    keyword_score,
    query_terms,
    recency_score,
)
from prepende_brain.private_fs import prepare_private_sqlite

KINDS = ("episodic", "semantic", "procedural")

# Default similarity thresholds for consolidation. Embedding cosine is strict
# (only true near-duplicates merge); lexical Jaccard is the fail-safe when a
# provider has no embeddings. Deliberately CONSERVATIVE — a wrong merge corrupts
# memory, while a missed merge just leaves two near-dups, so we bias hard toward
# not merging. Lexical is set high because shared structural vocabulary (a "Goal:"
# prefix, a repeated entity name) inflates Jaccard between genuinely distinct
# facts; only near-identical text should collapse.
_CONSOLIDATE_VEC_THRESHOLD = 0.92
_CONSOLIDATE_LEX_THRESHOLD = 0.85
# Lexical (no-embedding) merge is eligible only for SHORT, atomic memories. Two
# long same-topic texts (e.g. distinct conversation turns) share most of their
# vocabulary, so set-Jaccard falsely reads them as near-duplicates at any
# threshold. Long memories need real semantic similarity (embeddings) to be
# merged safely; without it, leave them alone.
_CONSOLIDATE_LEX_MAX_LEN = 240
_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "at", "is", "are",
    "its", "it", "for", "with", "as", "by", "that", "this", "be", "was", "were",
}


def _norm_tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", (text or "").lower())
            if len(t) >= 3 and t not in _STOPWORDS}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    union = len(a | b)
    return len(a & b) / union if union else 0.0


def _normalize_groups(raw: Any, n: int) -> list[list[int]]:
    """Coerce a grouper's output into valid index clusters: every index in 0..n-1
    appears exactly once; unknown/duplicate/out-of-range indices are dropped, and
    any index the grouper omitted becomes its own singleton (never merged by
    accident). Fail-safe: bad input -> all singletons (no merges)."""
    groups: list[list[int]] = []
    seen: set[int] = set()
    if isinstance(raw, list):
        for g in raw:
            members = []
            if isinstance(g, (list, tuple)):
                for idx in g:
                    try:
                        i = int(idx)
                    except Exception:
                        continue
                    if 0 <= i < n and i not in seen:
                        seen.add(i)
                        members.append(i)
            if members:
                groups.append(members)
    for i in range(n):
        if i not in seen:
            groups.append([i])
    return groups


def _similar(x: dict, y: dict, vec_thr: float, lex_thr: float, lex_max_len: int) -> bool:
    # Embeddings are reliable on long text too — no length guard on the vec path.
    if x["vec"] is not None and y["vec"] is not None:
        try:
            return _cosine(x["vec"], y["vec"]) >= vec_thr
        except Exception:
            pass
    # Lexical fail-safe: only for short atomic memories (see _CONSOLIDATE_LEX_MAX_LEN).
    if x["len"] > lex_max_len or y["len"] > lex_max_len:
        return False
    return _jaccard(x["tokens"], y["tokens"]) >= lex_thr


class SqliteMemoryStore(MemoryStore):
    name = "sqlite"

    def __init__(self, path: str = "./.engram/memory.db") -> None:
        self.path = prepare_private_sqlite(path)
        # Optional async embedder: texts -> vectors. Wired post-build (brain.py);
        # embeddings are stored per-row and are as sensitive as the plaintext.
        self._embedder = None
        with self._conn() as c:
            c.execute(
                "CREATE TABLE IF NOT EXISTS memories ("
                "id TEXT PRIMARY KEY, scope TEXT NOT NULL, content TEXT NOT NULL, "
                "metadata TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL, "
                "updated_at REAL, status TEXT NOT NULL DEFAULT 'active', deleted_at REAL)"
            )
            self._ensure_column(c, "updated_at", "REAL")
            self._ensure_column(c, "status", "TEXT NOT NULL DEFAULT 'active'")
            self._ensure_column(c, "deleted_at", "REAL")
            self._ensure_column(c, "kind", "TEXT NOT NULL DEFAULT 'episodic'")
            self._ensure_column(c, "valid_from", "REAL")
            self._ensure_column(c, "superseded_by", "TEXT")
            self._ensure_column(c, "embedding", "TEXT")
            c.execute(
                "CREATE TABLE IF NOT EXISTS edges ("
                "src TEXT NOT NULL, dst TEXT NOT NULL, relation TEXT NOT NULL)"
            )

    def set_embedder(self, embedder: Any) -> None:
        """Wire an async `texts -> vectors` callable (usually gateway.embed)."""
        self._embedder = embedder

    async def _embed(self, text: str) -> list[float] | None:
        """One vector, or None on ANY failure — recall must degrade, not break."""
        if self._embedder is None:
            return None
        try:
            vectors = await self._embedder([text])
            vec = list(vectors[0]) if vectors else None
            return vec if vec else None
        except NotImplementedError:
            self._embedder = None  # this provider has no embeddings — stop asking
            return None
        except Exception:
            return None  # transient failure — lexical this time, retry next call

    def _conn(self) -> sqlite3.Connection:
        prepare_private_sqlite(self.path)
        conn = sqlite3.connect(self.path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")     # concurrent readers + a writer
        conn.execute("PRAGMA busy_timeout=3000")    # wait then error, never hang
        prepare_private_sqlite(self.path)
        return conn

    def _ensure_column(self, conn: sqlite3.Connection, name: str, spec: str) -> None:
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(memories)").fetchall()}
        if name not in cols:
            conn.execute(f"ALTER TABLE memories ADD COLUMN {name} {spec}")

    async def write(self, content: str, *, scope: str, metadata: dict[str, Any] | None = None) -> str:
        mid = f"mem_{uuid.uuid4().hex[:12]}"
        now = time.time()
        meta = dict(metadata or {})
        kind = meta.get("kind") if meta.get("kind") in KINDS else "episodic"
        vec = await self._embed(content)
        with self._conn() as c:
            c.execute(
                "INSERT INTO memories (id, scope, content, metadata, created_at, updated_at, status, kind, valid_from, embedding) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (mid, scope, content, json.dumps(meta), now, now, "active", kind,
                 now, json.dumps(vec) if vec else None),
            )
        return mid

    async def embed_backfill(self, *, scope: str, limit: int = 200) -> dict[str, int]:
        """Embed live rows written while the embedder was absent or failing.
        write() embeds exactly once, so such rows keep embedding=NULL forever
        and hybrid recall scores them vec=0 — a freshly seeded brain loses to
        any old chatty memory. Per-scope and bounded; stops at the first embed
        failure (retry on a later call), never raises.
        Returns {"scanned", "embedded", "remaining"}."""
        live = "scope=? AND status != 'deleted' AND superseded_by IS NULL AND embedding IS NULL"

        def _remaining() -> int:
            with self._conn() as c:
                return c.execute(f"SELECT COUNT(*) FROM memories WHERE {live}", (scope,)).fetchone()[0]

        if self._embedder is None:
            return {"scanned": 0, "embedded": 0, "remaining": _remaining()}
        with self._conn() as c:
            rows = c.execute(
                f"SELECT id, content FROM memories WHERE {live} ORDER BY created_at LIMIT ?",
                (scope, limit)).fetchall()
        embedded = 0
        for r in rows:
            vec = await self._embed(r["content"])
            if vec is None:
                break  # embedder failing right now — the rest waits for the next call
            with self._conn() as c:
                c.execute("UPDATE memories SET embedding=?, updated_at=? WHERE id=?",
                          (json.dumps(vec), time.time(), r["id"]))
            embedded += 1
        return {"scanned": len(rows), "embedded": embedded, "remaining": _remaining()}

    @staticmethod
    def _row_dict(r: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": r["id"], "content": r["content"], "metadata": json.loads(r["metadata"]),
            "created_at": r["created_at"], "kind": r["kind"] or "episodic",
            "superseded_by": r["superseded_by"],
        }

    async def search(self, query: str, *, scope: str, k: int = 10) -> Sequence[Any]:
        terms = query_terms(query)
        qvec = await self._embed(query)
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM memories WHERE scope=? AND status != 'deleted' "
                "AND superseded_by IS NULL ORDER BY created_at DESC",
                (scope,),
            ).fetchall()
        if not rows:
            return []
        now = time.time()

        def kw_score(r: sqlite3.Row) -> float:
            return keyword_score(r["content"], terms)

        def vec_score(r: sqlite3.Row) -> float:
            if qvec is None or not r["embedding"]:
                return 0.0
            try:
                return _cosine(qvec, json.loads(r["embedding"]))
            except Exception:
                return 0.0

        semantic = qvec is not None

        # Weights come from memory/_scoring.py so backend parity is
        # structural, not copy-paste discipline.
        def score(r: sqlite3.Row) -> float:
            return _blend(vec_score(r), kw_score(r),
                          recency_score(r["created_at"], now), semantic=semantic)

        scored = sorted(rows, key=score, reverse=True)
        # Lexical parity with the original behavior: with no semantic signal and
        # no keyword hit anywhere, fall back to the most recent k.
        if not semantic and not any(kw_score(r) > 0 for r in rows):
            scored = rows  # already ordered by created_at desc
        return [self._row_dict(r) for r in scored[:k]]

    async def supersede(
        self, memory_id: str, content: str, *, scope: str, metadata: dict[str, Any] | None = None
    ) -> str | None:
        """Temporal validity: never overwrite a fact — write its successor and
        mark the old row superseded (excluded from recall, kept for audit),
        ATOMICALLY. Postgres parity: an already-superseded (or deleted/missing)
        target returns None instead of forking the chain, and a crash can never
        leave both facts active."""
        vec = await self._embed(content)  # model call stays OUTSIDE the txn
        new_id = f"mem_{uuid.uuid4().hex[:12]}"
        now = time.time()
        with self._conn() as c:
            # BEGIN IMMEDIATE takes the writer lock up front so check + insert
            # + mark run as one unit (the sqlite analogue of postgres's
            # SELECT ... FOR UPDATE): a concurrent supersede serializes behind
            # us, re-reads a row that is already superseded, and returns None.
            c.execute("BEGIN IMMEDIATE")
            old = c.execute(
                "SELECT kind FROM memories WHERE id=? AND scope=? "
                "AND status != 'deleted' AND superseded_by IS NULL",
                (memory_id, scope),
            ).fetchone()
            if old is None:
                return None
            meta = dict(metadata or {})
            meta.setdefault("kind", old["kind"] or "episodic")
            meta["supersedes"] = memory_id
            kind = meta["kind"] if meta["kind"] in KINDS else "episodic"
            c.execute(
                "INSERT INTO memories (id, scope, content, metadata, created_at, updated_at, status, kind, valid_from, embedding) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (new_id, scope, content, json.dumps(meta), now, now, "active", kind,
                 now, json.dumps(vec) if vec else None),
            )
            c.execute(
                "UPDATE memories SET superseded_by=?, updated_at=? "
                "WHERE id=? AND scope=? AND superseded_by IS NULL",
                (new_id, now, memory_id, scope),
            )
        return new_id

    async def fold_duplicate(self, duplicate_id: str, canonical_id: str, *, scope: str) -> bool:
        """Canonicalize an exact duplicate WITHOUT minting a new node: point
        the duplicate's superseded_by at the already-active canonical. After
        this, exactly one node (the canonical) answers recall; the duplicate
        is kept for audit. Atomic; refuses unless BOTH nodes are active and
        distinct. Reversed by unfold_duplicate."""
        if duplicate_id == canonical_id:
            return False
        now = time.time()
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            both = c.execute(
                "SELECT COUNT(*) FROM memories WHERE id IN (?, ?) AND scope=? "
                "AND status != 'deleted' AND superseded_by IS NULL",
                (duplicate_id, canonical_id, scope),
            ).fetchone()[0]
            if both != 2:
                return False
            c.execute("UPDATE memories SET superseded_by=?, updated_at=? WHERE id=? AND scope=?",
                      (canonical_id, now, duplicate_id, scope))
        return True

    async def unfold_duplicate(self, duplicate_id: str, canonical_id: str, *, scope: str) -> bool:
        """Reverse fold_duplicate: reactivate the duplicate. Refuses unless
        the duplicate points at exactly this canonical and the canonical is
        still active (so unfolding restores the pre-fold state, nothing else)."""
        now = time.time()
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            dup = c.execute(
                "SELECT id FROM memories WHERE id=? AND scope=? AND status != 'deleted' "
                "AND superseded_by=?",
                (duplicate_id, scope, canonical_id),
            ).fetchone()
            canon = c.execute(
                "SELECT id FROM memories WHERE id=? AND scope=? AND status != 'deleted' "
                "AND superseded_by IS NULL",
                (canonical_id, scope),
            ).fetchone()
            if dup is None or canon is None:
                return False
            c.execute("UPDATE memories SET superseded_by=NULL, updated_at=? WHERE id=? AND scope=?",
                      (now, duplicate_id, scope))
        return True

    async def rollback_supersession(self, new_id: str, *, scope: str) -> str | None:
        """Reverse ONE supersession edge: reactivate the predecessor and mark
        the successor as rolled back (kept for audit, excluded from recall by
        pointing its superseded_by BACK at the predecessor). Nothing is
        deleted; the single-active-node invariant holds throughout. Returns
        the restored predecessor id, or None when the successor is not the
        active head of a supersession edge (already rolled back, superseded
        again, deleted, or missing) — callers treat None as rollback failure."""
        now = time.time()
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute(
                "SELECT metadata FROM memories WHERE id=? AND scope=? "
                "AND status != 'deleted' AND superseded_by IS NULL",
                (new_id, scope),
            ).fetchone()
            if row is None:
                return None
            predecessor = (json.loads(row["metadata"] or "{}") or {}).get("supersedes")
            if not predecessor:
                return None
            old = c.execute(
                "SELECT id FROM memories WHERE id=? AND scope=? "
                "AND status != 'deleted' AND superseded_by=?",
                (predecessor, scope, new_id),
            ).fetchone()
            if old is None:
                return None  # chain does not point back here — refuse, don't guess
            c.execute("UPDATE memories SET superseded_by=NULL, updated_at=? WHERE id=? AND scope=?",
                      (now, predecessor, scope))
            c.execute("UPDATE memories SET superseded_by=?, updated_at=? WHERE id=? AND scope=?",
                      (predecessor, now, new_id, scope))
        return str(predecessor)

    async def update(
        self,
        memory_id: str,
        *,
        scope: str,
        content: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Any | None:
        updates: list[str] = ["updated_at=?"]
        params: list[Any] = [time.time()]
        if content is not None:
            updates.append("content=?")
            params.append(content)
            # Content changed -> the stored vector describes the OLD text. Keep
            # the embedding==content invariant (write/supersede both embed) or
            # semantic recall scores this row against its old meaning. An embed
            # failure (None) NULLs it: lexical-only beats confidently-wrong.
            vec = await self._embed(content)
            updates.append("embedding=?")
            params.append(json.dumps(vec) if vec else None)
        if metadata is not None:
            updates.append("metadata=?")
            params.append(json.dumps(metadata))
        params.extend([memory_id, scope])
        with self._conn() as c:
            cur = c.execute(
                f"UPDATE memories SET {', '.join(updates)} WHERE id=? AND scope=? AND status != 'deleted'",
                params,
            )
            if cur.rowcount == 0:
                return None
            row = c.execute(
                "SELECT * FROM memories WHERE id=? AND scope=? AND status != 'deleted'",
                (memory_id, scope),
            ).fetchone()
        if row is None:
            return None
        return {"id": row["id"], "content": row["content"], "metadata": json.loads(row["metadata"]), "created_at": row["created_at"]}

    async def delete(self, memory_id: str, *, scope: str) -> bool:
        now = time.time()
        with self._conn() as c:
            cur = c.execute(
                "UPDATE memories SET status='deleted', deleted_at=?, updated_at=? WHERE id=? AND scope=? AND status != 'deleted'",
                (now, now, memory_id, scope),
            )
            return cur.rowcount > 0

    async def consolidate(
        self,
        *,
        scope: str,
        sim_threshold: float | None = None,
        min_cluster: int = 2,
        summarizer: Any = None,
        grouper: Any = None,
    ) -> dict[str, Any]:
        """Make memory MORE useful, not just bigger: dedup near-duplicates.

        Clusters active memories in `scope` by similarity (embedding cosine when
        rows carry vectors, lexical Jaccard otherwise — the fail-safe), then for
        each cluster keeps ONE canonical and marks the rest `superseded_by` it.
        Superseded rows are excluded from recall (see `search`) but kept for
        audit — never deleted. Non-destructive and idempotent.

        Deterministic by default (no model calls — safe for scheduled heal). If an
        async `summarizer(list[str]) -> str` is injected, each cluster is collapsed
        into one fresh summary memory instead of keeping the longest member.

        For TOPIC-level consolidation (grouping same-topic *distinct* facts, not
        just near-duplicates), inject an async `grouper(list[str]) -> list[list[int]]`
        that returns index clusters. This replaces the built-in similarity
        clustering — the store keeps owning the safe supersede mechanics while the
        caller injects the (e.g. model-driven) intelligence. A grouper failure
        fails safe to no merges; a summarizer failure skips that cluster (grouper
        clusters hold distinct facts, so there is no safe deterministic fallback
        for them). See memory/consolidator.py.

        Returns a report; callers may ignore it. `sim_threshold` overrides both
        defaults when given.
        """
        with self._conn() as c:
            rows = c.execute(
                "SELECT id, content, created_at, embedding FROM memories "
                "WHERE scope=? AND status != 'deleted' AND superseded_by IS NULL "
                "ORDER BY created_at ASC",
                (scope,),
            ).fetchall()
        report: dict[str, Any] = {
            "scope": scope, "before": len(rows), "after": len(rows),
            "clusters_merged": 0, "superseded": 0, "method": "lexical", "threshold": None,
            "merges": [],  # audit trail: each {canonical, canonical_content, superseded:[{id,content}]}
        }
        if len(rows) < 2:
            return report

        items: list[dict[str, Any]] = []
        any_vec = False
        for r in rows:
            vec = None
            if r["embedding"]:
                try:
                    parsed = json.loads(r["embedding"])
                    if parsed:
                        vec = parsed
                        any_vec = True
                except Exception:
                    vec = None
            items.append({
                "id": r["id"], "content": r["content"] or "", "vec": vec,
                "tokens": _norm_tokens(r["content"] or ""),
                "len": len(r["content"] or ""), "created": r["created_at"] or 0.0,
            })
        vec_thr = sim_threshold if sim_threshold is not None else _CONSOLIDATE_VEC_THRESHOLD
        lex_thr = sim_threshold if sim_threshold is not None else _CONSOLIDATE_LEX_THRESHOLD

        if grouper is not None:
            # Injected (e.g. model-driven) topic clustering. Fail safe to no merges.
            report["method"] = "grouper"
            report["threshold"] = None
            try:
                raw = await grouper([it["content"] for it in items])
            except Exception:
                raw = None
            group_list = _normalize_groups(raw, len(items))
        else:
            report["method"] = "embedding" if any_vec else "lexical"
            report["threshold"] = vec_thr if any_vec else lex_thr
            # Union-find: group all rows that are pairwise near-duplicates.
            parent = list(range(len(items)))

            def find(x: int) -> int:
                while parent[x] != x:
                    parent[x] = parent[parent[x]]
                    x = parent[x]
                return x

            def union(a: int, b: int) -> None:
                ra, rb = find(a), find(b)
                if ra != rb:
                    parent[rb] = ra

            for i in range(len(items)):
                for j in range(i + 1, len(items)):
                    if _similar(items[i], items[j], vec_thr, lex_thr, _CONSOLIDATE_LEX_MAX_LEN):
                        union(i, j)

            groups: dict[int, list[int]] = {}
            for idx in range(len(items)):
                groups.setdefault(find(idx), []).append(idx)
            group_list = list(groups.values())

        now = time.time()
        for members in group_list:
            if len(members) < min_cluster:
                continue
            canonical_id: str | None = None
            canonical_content: str = ""
            targets: list[int] = []
            if summarizer is not None:
                try:
                    merged = await summarizer([items[k]["content"] for k in members])
                except Exception:
                    merged = None
                if merged and str(merged).strip():
                    canonical_content = str(merged).strip()
                    canonical_id = await self.write(
                        canonical_content, scope=scope,
                        metadata={"kind": "semantic",
                                  "consolidated_from": [items[k]["id"] for k in members]},
                    )
                    targets = members  # supersede every original by the new summary
            if canonical_id is None:
                if grouper is not None:
                    # Grouper clusters hold DISTINCT same-topic facts, not
                    # near-duplicates — keeping one member would erase the rest.
                    # A summarizer failure therefore fails safe like a grouper
                    # failure: skip the cluster, leave its rows untouched.
                    continue
                # keep-NEWEST (tie-break: longest) as the canonical survivor —
                # near-duplicates are often correction rewrites, and preferring
                # length would resurrect an older stale value over its fix.
                canonical = max(members, key=lambda k: (items[k]["created"], items[k]["len"]))
                canonical_id = items[canonical]["id"]
                canonical_content = items[canonical]["content"]
                targets = [k for k in members if k != canonical]
            report["clusters_merged"] += 1
            merge_rec: dict[str, Any] = {
                "canonical": canonical_id, "canonical_content": canonical_content[:240],
                "superseded": [],
            }
            with self._conn() as c:
                for k in targets:
                    cur = c.execute(
                        "UPDATE memories SET superseded_by=?, updated_at=? "
                        "WHERE id=? AND scope=? AND superseded_by IS NULL",
                        (canonical_id, now, items[k]["id"], scope),
                    )
                    if cur.rowcount:
                        report["superseded"] += cur.rowcount
                        merge_rec["superseded"].append(
                            {"id": items[k]["id"], "content": items[k]["content"][:160]})
            if merge_rec["superseded"]:
                report["merges"].append(merge_rec)

        with self._conn() as c:
            report["after"] = c.execute(
                "SELECT count(*) FROM memories WHERE scope=? AND status != 'deleted' "
                "AND superseded_by IS NULL",
                (scope,),
            ).fetchone()[0]
        return report

    async def link(self, src_id: str, dst_id: str, *, relation: str) -> None:
        with self._conn() as c:
            c.execute("INSERT INTO edges (src, dst, relation) VALUES (?,?,?)", (src_id, dst_id, relation))
