"""PostgresMemoryStore — Engram's memory on Postgres / Supabase. Same interface.

Drop-in for SqliteMemoryStore (implements kernel.contracts.MemoryStore) at full
parity: typed rows (episodic | semantic | procedural), supersede-not-overwrite,
hybrid recall (keyword + vector cosine + recency, identical weights via
memory/_scoring.py), an optional embedder hook with fail-safe degradation, and
ids in the same `mem_<hex12>` format so a sqlite -> postgres backfill keeps
every id and supersede chain intact.

Tables: `public.engram_kernel_memories` / `public.engram_kernel_memory_edges`
(NOT `memories` / `engram_memories` — those belong to the auth.users-keyed
consumer-app lane; this lane is keyed by `scope`, the tenant slug). Canonical
DDL incl. RLS lives in supabase/migrations/019 + 021; `_ensure` bootstraps a
bare local postgres with the same tables minus the Supabase-specific
roles/policies, and merely VERIFIES on a migrated database where the
substrate's least-privilege role (engram_brain) has no DDL.

Tenant isolation, in depth:
  1. every statement here filters by scope in WHERE — app-level, always on —
     and an empty/whitespace scope is rejected before any SQL;
  2. every operation runs in a transaction that sets the transaction-local GUC
     `app.engram_scope`, which the FORCED row-level-security policy checks —
     verified live; note Supabase's default `postgres` role carries BYPASSRLS,
     so production runs as `engram_brain` (no BYPASSRLS, kernel tables only);
  3. anon/authenticated get no grants at all — unreachable from PostgREST.

EVENT LOOPS (review finding, gate 1): callers like interface/v1_api.py run
`asyncio.run(...)` per request, so a single cached pool would bind to the
first request's (closed) loop and break every later request. Pools are
therefore keyed BY RUNNING LOOP, created under a per-loop lock, and entries
for closed loops are dropped. The cost is honest: per-`asyncio.run` callers
pay a fresh connection per loop — acceptable at alpha scale; a persistent
event loop in the API is the later optimization.

Embeddings are stored as jsonb (any dimension — parity with sqlite) plus a
pgvector `embedding_vec vector(1536)` column maintained when the vector is
1536-dim. Recall candidates: newest _SCAN_LIMIT rows, topped up by an ILIKE
keyword sweep (so lexical recall does NOT silently lose old facts the way a
bare window would) and, when semantic, pgvector ANN pre-selection.
Degradation of the vector column is taken ONLY on vector-specific errors and
is always printed to stderr — never silent, never triggered by a transient
network blip.

asyncpg is an OPTIONAL dependency (pip install asyncpg; Apache-2.0). If it's
missing, memory/factory.py falls back to sqlite, so nothing breaks without it.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
import uuid
from typing import Any, Sequence

from kernel.contracts import MemoryStore
from memory._scoring import blend, cosine, keyword_score, query_terms, recency_score

KINDS = ("episodic", "semantic", "procedural")

_SCAN_LIMIT = 500
_ANN_LIMIT = 200
_KEYWORD_LIMIT = 200
_CONSOLIDATE_VEC_THRESHOLD = 0.92
_CONSOLIDATE_LEX_THRESHOLD = 0.85
_CONSOLIDATE_LEX_MAX_LEN = 240
_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "at", "is", "are",
    "its", "it", "for", "with", "as", "by", "that", "this", "be", "was", "were",
}
# pgvector column dimension follows the chosen embedder (OpenAI=1536, others via
# EMBEDDING_DIM). Default 1536 keeps the existing Supabase column unchanged. On a
# hosted deploy set EMBEDDING_DIM as a real env var so it is present at import.
_VECTOR_DIM = int(
    (os.environ.get("EMBEDDING_DIM", "") or os.environ.get("ENGRAM_EMBEDDING_DIMENSIONS", "") or "1536")
    or 1536
)

_TABLE = "public.engram_kernel_memories"
_EDGES = "public.engram_kernel_memory_edges"

# Error types that genuinely mean "this database can't do pgvector here" —
# the ONLY grounds for degrading the vector column. Everything else re-raises.
_VECTOR_ERROR_TYPES = (
    "UndefinedColumnError", "UndefinedObjectError", "UndefinedFunctionError",
    "DataError", "InvalidTextRepresentationError", "CannotCoerceError",
)

_BOOTSTRAP = f"""
CREATE TABLE IF NOT EXISTS {_TABLE} (
    id           text PRIMARY KEY,
    scope        text NOT NULL CHECK (scope <> ''),
    content      text NOT NULL,
    metadata     jsonb NOT NULL DEFAULT '{{}}'::jsonb,
    kind         text NOT NULL DEFAULT 'episodic' CHECK (kind IN ('episodic','semantic','procedural')),
    status       text NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived','deleted')),
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz,
    deleted_at   timestamptz,
    valid_from   timestamptz,
    superseded_by text REFERENCES {_TABLE}(id) ON DELETE SET NULL,
    embedding    jsonb
);
CREATE INDEX IF NOT EXISTS engram_kernel_memories_scope_created_idx
    ON {_TABLE} (scope, created_at DESC);
CREATE TABLE IF NOT EXISTS {_EDGES} (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    scope      text NOT NULL CHECK (scope <> ''),
    src        text NOT NULL REFERENCES {_TABLE}(id) ON DELETE CASCADE,
    dst        text NOT NULL REFERENCES {_TABLE}(id) ON DELETE CASCADE,
    relation   text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
"""


def _vec_literal(vec: Sequence[float]) -> str:
    return "[" + ",".join(f"{x:.8g}" for x in vec) + "]"


def _is_vector_error(exc: Exception) -> bool:
    return type(exc).__name__ in _VECTOR_ERROR_TYPES


def _norm_tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", (text or "").lower())
            if len(t) >= 3 and t not in _STOPWORDS}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    union = len(a | b)
    return len(a & b) / union if union else 0.0


def _normalize_groups(raw: Any, n: int) -> list[list[int]]:
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
    if x["vec"] is not None and y["vec"] is not None:
        try:
            return cosine(x["vec"], y["vec"]) >= vec_thr
        except Exception:
            pass
    if x["len"] > lex_max_len or y["len"] > lex_max_len:
        return False
    return _jaccard(x["tokens"], y["tokens"]) >= lex_thr


class PostgresMemoryStore(MemoryStore):
    name = "postgres"

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        # Pools are per event loop (see module docstring); guarded per loop.
        self._pools: dict[Any, Any] = {}
        self._locks: dict[Any, asyncio.Lock] = {}
        self._bootstrapped = False
        self._has_vector = False
        self._vector_type = "vector"  # schema-qualified once detected in _ensure
        # Optional async embedder: texts -> vectors. Wired post-build (brain.py);
        # embeddings are stored per-row and are as sensitive as the plaintext.
        self._embedder = None

    def set_embedder(self, embedder: Any) -> None:
        """Wire an async `texts -> vectors` callable (usually gateway.embed)."""
        self._embedder = embedder

    @staticmethod
    def _check_scope(scope: str) -> str:
        scope = (scope or "").strip()
        if not scope:
            # An empty scope would match the RLS policy's unset-GUC sentinel —
            # reject it before SQL so the hole can't open from the app side.
            raise ValueError("memory scope must be a non-empty tenant slug")
        return scope

    async def _embed(self, text: str) -> list[float] | None:
        """One float vector, or None on ANY failure — recall degrades, never breaks."""
        if self._embedder is None:
            return None
        try:
            vectors = await self._embedder([text])
            vec = [float(x) for x in vectors[0]] if vectors else None
            return vec if vec else None
        except NotImplementedError:
            self._embedder = None  # this provider has no embeddings — stop asking
            return None
        except Exception:
            return None  # transient/malformed — lexical this time, retry next call

    def _drop_vector(self, where: str, exc: Exception) -> None:
        """Degradation is never silent (review finding, gate 1)."""
        self._has_vector = False
        print(f"engram memory: pgvector disabled after {type(exc).__name__} in {where} — "
              "recall continues lexically; new rows skip embedding_vec.", file=sys.stderr)

    async def _ensure(self):
        loop = asyncio.get_running_loop()
        pool = self._pools.get(loop)
        if pool is not None:
            return pool
        # Drop references to pools whose loops are gone (their connections
        # died with the loop; nothing left to close cleanly).
        for stale in [lp for lp in self._pools if lp.is_closed()]:
            self._pools.pop(stale, None)
            self._locks.pop(stale, None)
        lock = self._locks.setdefault(loop, asyncio.Lock())
        async with lock:
            pool = self._pools.get(loop)
            if pool is not None:
                return pool
            import asyncpg  # imported lazily so the dep is optional

            pool = await asyncpg.create_pool(self.dsn, min_size=1, max_size=5)
            async with pool.acquire() as con:
                # Bootstrap DDL is for bare/local postgres. In production the
                # brain runs as a least-privilege role (engram_brain: no DDL,
                # no BYPASSRLS) against a migrated database — DDL is denied
                # there and that's correct; verify the table instead.
                try:
                    await con.execute(_BOOTSTRAP)
                except Exception:
                    pass
                exists = await con.fetchrow(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema='public' AND table_name='engram_kernel_memories'"
                )
                if exists is None:
                    raise RuntimeError(
                        "engram_kernel_memories is missing and this role cannot create it — "
                        "apply supabase/migrations/019_engram_kernel_memory.sql as admin first")
                # pgvector column is best-effort: present on Supabase (the
                # migration creates it), attempted here for bare postgres,
                # absent without the extension — recall works either way.
                for ddl in (
                    f"ALTER TABLE {_TABLE} ADD COLUMN IF NOT EXISTS embedding_vec vector({_VECTOR_DIM})",
                    f"ALTER TABLE {_TABLE} ADD COLUMN IF NOT EXISTS embedding_vec extensions.vector({_VECTOR_DIM})",
                ):
                    try:
                        await con.execute(ddl)
                        break
                    except Exception:
                        continue
                row = await con.fetchrow(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_schema='public' AND table_name='engram_kernel_memories' "
                    "AND column_name='embedding_vec'"
                )
                self._has_vector = row is not None
                if self._has_vector:
                    # The cast must name pgvector's actual schema and be safely
                    # quoted; prefer the extensions schema when both exist.
                    qualified = await con.fetchval(
                        "SELECT format('%I.%I', n.nspname, t.typname) "
                        "FROM pg_type t JOIN pg_namespace n ON t.typnamespace = n.oid "
                        "WHERE t.typname = 'vector' AND t.typtype = 'b' "
                        "ORDER BY (n.nspname = 'extensions') DESC LIMIT 1"
                    )
                    if qualified:
                        self._vector_type = qualified
            self._pools[loop] = pool
            return pool

    async def _scoped(self, con, scope: str) -> None:
        """Transaction-local tenant scope for the RLS policy (defense layer 2)."""
        await con.execute("SELECT set_config('app.engram_scope', $1, true)", scope)

    @staticmethod
    def _meta(raw: Any) -> dict[str, Any]:
        if isinstance(raw, dict):
            return raw
        return json.loads(raw or "{}")

    def _row_dict(self, r: Any) -> dict[str, Any]:
        return {
            "id": r["id"],
            "content": r["content"],
            "metadata": self._meta(r["metadata"]),
            "created_at": r["created_at"].timestamp(),
            "kind": r["kind"] or "episodic",
            "superseded_by": r["superseded_by"],
        }

    async def _insert_row(self, con, *, mid: str, scope: str, content: str,
                          meta: dict[str, Any], kind: str, vec: list[float] | None) -> None:
        """One INSERT, shared by write() and supersede(); caller holds the
        scoped transaction. Vector-column failure degrades (loudly) and falls
        back to the no-vector insert INSIDE the same transaction via savepoint."""
        cols = "id, scope, content, metadata, kind, status, updated_at, valid_from, embedding"
        args: list[Any] = [mid, scope, content, json.dumps(meta), kind,
                           json.dumps(vec) if vec else None]
        placeholders = "$1,$2,$3,$4::jsonb,$5,'active',now(),now(),$6::jsonb"
        if self._has_vector and vec and len(vec) == _VECTOR_DIM:
            try:
                async with con.transaction():  # savepoint inside the outer txn
                    await con.execute(
                        f"INSERT INTO {_TABLE} ({cols}, embedding_vec) "
                        f"VALUES ({placeholders},$7::{self._vector_type})",
                        *args, _vec_literal(vec))
                return
            except Exception as exc:
                if not _is_vector_error(exc):
                    raise
                self._drop_vector("write", exc)
        await con.execute(f"INSERT INTO {_TABLE} ({cols}) VALUES ({placeholders})", *args)

    async def write(self, content: str, *, scope: str, metadata: dict[str, Any] | None = None) -> str:
        scope = self._check_scope(scope)
        pool = await self._ensure()
        mid = f"mem_{uuid.uuid4().hex[:12]}"
        meta = dict(metadata or {})
        kind = meta.get("kind") if meta.get("kind") in KINDS else "episodic"
        vec = await self._embed(content)
        async with pool.acquire() as con:
            async with con.transaction():
                await self._scoped(con, scope)
                await self._insert_row(con, mid=mid, scope=scope, content=content,
                                       meta=meta, kind=kind, vec=vec)
        return mid

    async def embed_backfill(self, *, scope: str, limit: int = 200) -> dict[str, int]:
        """Embed live rows written while the embedder was absent or failing.
        write() embeds exactly once, so such rows keep embedding=NULL forever
        and hybrid recall scores them vec=0 — a freshly seeded brain loses to
        any old chatty memory. Per-scope (RLS: executors take scopes from
        config, never discovery) and bounded; stops at the first embed failure,
        never raises. embedding_vec follows the same degradation contract as
        write/update: vector-specific errors drop the column, everything else
        re-raises. Returns {"scanned", "embedded", "remaining"}."""
        scope = self._check_scope(scope)
        pool = await self._ensure()
        live = ("scope=$1 AND status != 'deleted' AND superseded_by IS NULL "
                "AND embedding IS NULL")

        async def _remaining() -> int:
            async with pool.acquire() as con:
                async with con.transaction():
                    await self._scoped(con, scope)
                    return int(await con.fetchval(
                        f"SELECT COUNT(*) FROM {_TABLE} WHERE {live}", scope))

        if self._embedder is None:
            return {"scanned": 0, "embedded": 0, "remaining": await _remaining()}
        async with pool.acquire() as con:
            async with con.transaction():
                await self._scoped(con, scope)
                rows = await con.fetch(
                    f"SELECT id, content FROM {_TABLE} WHERE {live} "
                    f"ORDER BY created_at LIMIT {int(limit)}", scope)
        embedded = 0
        for r in rows:
            vec = await self._embed(r["content"])
            if vec is None:
                break  # embedder failing right now — the rest waits for the next call
            async with pool.acquire() as con:
                async with con.transaction():
                    await self._scoped(con, scope)
                    applied = False
                    if self._has_vector and len(vec) == _VECTOR_DIM:
                        try:
                            async with con.transaction():  # savepoint
                                await con.execute(
                                    f"UPDATE {_TABLE} SET embedding=$3::jsonb, "
                                    f"embedding_vec=$4::{self._vector_type}, updated_at=now() "
                                    f"WHERE id=$2 AND scope=$1",
                                    scope, r["id"], json.dumps(vec), _vec_literal(vec))
                            applied = True
                        except Exception as exc:
                            if not _is_vector_error(exc):
                                raise
                            self._drop_vector("embed_backfill", exc)
                    if not applied:
                        await con.execute(
                            f"UPDATE {_TABLE} SET embedding=$3::jsonb, updated_at=now() "
                            f"WHERE id=$2 AND scope=$1",
                            scope, r["id"], json.dumps(vec))
            embedded += 1
        return {"scanned": len(rows), "embedded": embedded, "remaining": await _remaining()}

    async def search(self, query: str, *, scope: str, k: int = 10) -> Sequence[Any]:
        scope = self._check_scope(scope)
        pool = await self._ensure()
        terms = query_terms(query)
        qvec = await self._embed(query)
        live = "scope=$1 AND status != 'deleted' AND superseded_by IS NULL"
        async with pool.acquire() as con:
            async with con.transaction():
                await self._scoped(con, scope)
                rows = list(await con.fetch(
                    f"SELECT * FROM {_TABLE} WHERE {live} "
                    f"ORDER BY created_at DESC LIMIT {_SCAN_LIMIT}",
                    scope,
                ))
            window_full = len(rows) == _SCAN_LIMIT
            seen = {r["id"] for r in rows}
            # Past the scan window, keyword matches older than the window must
            # still be candidates — sqlite scans everything; a silent window
            # would change what the brain recalls when the backend swaps.
            if window_full and terms:
                patterns = [f"%{t}%" for t in terms[:8]]
                # The window already holds the newest rows, so spend the top-up
                # LIMIT strictly on rows OLDER than the window — without this
                # bound a recurring term just re-fetches rows already in `seen`
                # and older matches stay unreachable forever.
                oldest_in_window = rows[-1]["created_at"]
                async with con.transaction():
                    await self._scoped(con, scope)
                    kw = await con.fetch(
                        f"SELECT * FROM {_TABLE} WHERE {live} "
                        f"AND created_at < $3 "
                        f"AND content ILIKE ANY($2::text[]) "
                        f"ORDER BY created_at DESC LIMIT {_KEYWORD_LIMIT}",
                        scope, patterns, oldest_in_window,
                    )
                rows.extend(r for r in kw if r["id"] not in seen)
                seen.update(r["id"] for r in kw)
            # Semantic top-up: pgvector ANN pre-selection (an optimization,
            # never a dependency; vector-specific failures degrade loudly).
            if (window_full and qvec is not None and len(qvec) == _VECTOR_DIM
                    and self._has_vector):
                try:
                    async with con.transaction():
                        await self._scoped(con, scope)
                        ann = await con.fetch(
                            f"SELECT * FROM {_TABLE} WHERE {live} AND embedding_vec IS NOT NULL "
                            f"ORDER BY embedding_vec <=> $2::{self._vector_type} LIMIT {_ANN_LIMIT}",
                            scope, _vec_literal(qvec),
                        )
                    rows.extend(r for r in ann if r["id"] not in seen)
                except Exception as exc:
                    if not _is_vector_error(exc):
                        raise
                    self._drop_vector("search", exc)
        if not rows:
            return []
        now = time.time()
        semantic = qvec is not None

        def row_vec(r: Any) -> float:
            if qvec is None or not r["embedding"]:
                return 0.0
            try:
                stored = r["embedding"]
                stored = stored if isinstance(stored, list) else json.loads(stored)
                return cosine(qvec, stored)
            except Exception:
                return 0.0

        def score(r: Any) -> float:
            return blend(
                row_vec(r),
                keyword_score(r["content"], terms),
                recency_score(r["created_at"].timestamp(), now),
                semantic=semantic,
            )

        scored = sorted(rows, key=score, reverse=True)
        # Lexical parity with sqlite: no semantic signal and no keyword hit
        # anywhere -> fall back to the most recent k.
        if not semantic and not any(keyword_score(r["content"], terms) > 0 for r in rows):
            scored = rows  # already ordered by created_at desc
        return [self._row_dict(r) for r in scored[:k]]

    async def supersede(
        self, memory_id: str, content: str, *, scope: str, metadata: dict[str, Any] | None = None
    ) -> str | None:
        """Temporal validity: never overwrite a fact — write its successor and
        mark the old row superseded, ATOMICALLY (review finding, gate 1: a
        crash or a concurrent supersede must never leave both facts active).
        SELECT ... FOR UPDATE serializes racers; the loser re-reads a row that
        is already superseded and returns None."""
        scope = self._check_scope(scope)
        pool = await self._ensure()
        vec = await self._embed(content)  # model call stays OUTSIDE the txn
        new_id = f"mem_{uuid.uuid4().hex[:12]}"
        async with pool.acquire() as con:
            async with con.transaction():
                await self._scoped(con, scope)
                old = await con.fetchrow(
                    f"SELECT id, kind FROM {_TABLE} "
                    "WHERE id=$1 AND scope=$2 AND status != 'deleted' "
                    "AND superseded_by IS NULL FOR UPDATE",
                    memory_id, scope,
                )
                if old is None:
                    return None
                meta = dict(metadata or {})
                meta.setdefault("kind", old["kind"] or "episodic")
                meta["supersedes"] = memory_id
                kind = meta["kind"] if meta["kind"] in KINDS else "episodic"
                await self._insert_row(con, mid=new_id, scope=scope, content=content,
                                       meta=meta, kind=kind, vec=vec)
                await con.execute(
                    f"UPDATE {_TABLE} SET superseded_by=$1, updated_at=now() "
                    "WHERE id=$2 AND scope=$3",
                    new_id, memory_id, scope,
                )
        return new_id

    async def update(
        self,
        memory_id: str,
        *,
        scope: str,
        content: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Any | None:
        scope = self._check_scope(scope)
        pool = await self._ensure()
        assignments = ["updated_at=now()"]
        values: list[Any] = [memory_id, scope]
        idx = 3
        vec: list[float] | None = None
        if content is not None:
            assignments.append(f"content=${idx}")
            values.append(content)
            idx += 1
            # Content changed -> the stored vector describes the OLD text. Keep
            # the embedding==content invariant (write/supersede both embed) or
            # semantic recall scores this row against its old meaning. An embed
            # failure (None) NULLs it: lexical-only beats confidently-wrong.
            vec = await self._embed(content)
            assignments.append(f"embedding=${idx}::jsonb")
            values.append(json.dumps(vec) if vec else None)
            idx += 1
        if metadata is not None:
            assignments.append(f"metadata=${idx}::jsonb")
            values.append(json.dumps(metadata))
            idx += 1
        # embedding_vec follows the same invariant: NULL when the new vector is
        # unusable, so the ANN pre-selection can't rank the row by stale meaning.
        vec_assign: str | None = None
        vec_args: list[Any] = []
        if content is not None and self._has_vector:
            if vec and len(vec) == _VECTOR_DIM:
                vec_assign = f"embedding_vec=${idx}::{self._vector_type}"
                vec_args = [_vec_literal(vec)]
            else:
                vec_assign = "embedding_vec=NULL"
        where = "WHERE id=$1 AND scope=$2 AND status != 'deleted' RETURNING *"
        async with pool.acquire() as con:
            async with con.transaction():
                await self._scoped(con, scope)
                row = None
                applied = False
                if vec_assign is not None:
                    try:
                        async with con.transaction():  # savepoint inside the outer txn
                            row = await con.fetchrow(
                                f"UPDATE {_TABLE} SET {', '.join(assignments + [vec_assign])} {where}",
                                *values, *vec_args,
                            )
                        applied = True
                    except Exception as exc:
                        if not _is_vector_error(exc):
                            raise
                        self._drop_vector("update", exc)
                if not applied:
                    row = await con.fetchrow(
                        f"UPDATE {_TABLE} SET {', '.join(assignments)} {where}",
                        *values,
                    )
        if row is None:
            return None
        return {
            "id": row["id"],
            "content": row["content"],
            "metadata": self._meta(row["metadata"]),
            "created_at": row["created_at"].timestamp(),
        }

    async def delete(self, memory_id: str, *, scope: str) -> bool:
        scope = self._check_scope(scope)
        pool = await self._ensure()
        async with pool.acquire() as con:
            async with con.transaction():
                await self._scoped(con, scope)
                result = await con.execute(
                    f"UPDATE {_TABLE} SET status='deleted', deleted_at=now(), updated_at=now() "
                    "WHERE id=$1 AND scope=$2 AND status != 'deleted'",
                    memory_id, scope,
                )
        return not result.endswith(" 0")

    async def consolidate(
        self,
        *,
        scope: str,
        sim_threshold: float | None = None,
        min_cluster: int = 2,
        summarizer: Any = None,
        grouper: Any = None,
    ) -> dict[str, Any]:
        """Dedup active memories with sqlite parity: supersede, never delete."""
        scope = self._check_scope(scope)
        pool = await self._ensure()
        async with pool.acquire() as con:
            async with con.transaction():
                await self._scoped(con, scope)
                rows = await con.fetch(
                    f"SELECT id, content, EXTRACT(EPOCH FROM created_at)::float8 AS created, embedding "
                    f"FROM {_TABLE} "
                    "WHERE scope=$1 AND status != 'deleted' AND superseded_by IS NULL "
                    "ORDER BY created_at ASC",
                    scope,
                )

        report: dict[str, Any] = {
            "scope": scope, "before": len(rows), "after": len(rows),
            "clusters_merged": 0, "superseded": 0, "method": "lexical", "threshold": None,
            "merges": [],
        }
        if len(rows) < 2:
            return report

        items: list[dict[str, Any]] = []
        any_vec = False
        for r in rows:
            vec = None
            raw_vec = r["embedding"]
            if raw_vec:
                try:
                    parsed = raw_vec if isinstance(raw_vec, list) else json.loads(raw_vec)
                    if parsed:
                        vec = [float(x) for x in parsed]
                        any_vec = True
                except Exception:
                    vec = None
            content = r["content"] or ""
            items.append({
                "id": r["id"], "content": content, "vec": vec,
                "tokens": _norm_tokens(content), "len": len(content),
                "created": r["created"] or 0.0,
            })

        vec_thr = sim_threshold if sim_threshold is not None else _CONSOLIDATE_VEC_THRESHOLD
        lex_thr = sim_threshold if sim_threshold is not None else _CONSOLIDATE_LEX_THRESHOLD

        if grouper is not None:
            report["method"] = "grouper"
            try:
                raw = await grouper([it["content"] for it in items])
            except Exception:
                raw = None
            group_list = _normalize_groups(raw, len(items))
        else:
            report["method"] = "embedding" if any_vec else "lexical"
            report["threshold"] = vec_thr if any_vec else lex_thr
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

        for members in group_list:
            if len(members) < min_cluster:
                continue
            canonical_id: str | None = None
            canonical_content = ""
            targets: list[int] = []
            summary_vec: list[float] | None = None
            summary_meta: dict[str, Any] | None = None
            if summarizer is not None:
                try:
                    merged = await summarizer([items[k]["content"] for k in members])
                except Exception:
                    merged = None
                if merged and str(merged).strip():
                    canonical_content = str(merged).strip()
                    canonical_id = f"mem_{uuid.uuid4().hex[:12]}"
                    summary_meta = {
                        "kind": "semantic",
                        "consolidated_from": [items[k]["id"] for k in members],
                    }
                    summary_vec = await self._embed(canonical_content)
                    targets = members
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
            if not targets:
                continue
            report["clusters_merged"] += 1

            merge_rec: dict[str, Any] = {
                "canonical": canonical_id, "canonical_content": canonical_content[:240],
                "superseded": [],
            }
            target_ids = [items[k]["id"] for k in targets]
            async with pool.acquire() as con:
                async with con.transaction():
                    await self._scoped(con, scope)
                    if summary_meta is not None:
                        await self._insert_row(
                            con, mid=canonical_id, scope=scope, content=canonical_content,
                            meta=summary_meta, kind="semantic", vec=summary_vec,
                        )
                    changed = await con.fetch(
                        f"UPDATE {_TABLE} SET superseded_by=$1, updated_at=now() "
                        "WHERE scope=$2 AND id = ANY($3::text[]) AND status != 'deleted' "
                        "AND superseded_by IS NULL RETURNING id",
                        canonical_id, scope, target_ids,
                    )
            changed_ids = {r["id"] for r in changed}
            report["superseded"] += len(changed_ids)
            for k in targets:
                if items[k]["id"] in changed_ids:
                    merge_rec["superseded"].append(
                        {"id": items[k]["id"], "content": items[k]["content"][:160]})
            if merge_rec["superseded"]:
                report["merges"].append(merge_rec)

        async with pool.acquire() as con:
            async with con.transaction():
                await self._scoped(con, scope)
                report["after"] = await con.fetchval(
                    f"SELECT count(*) FROM {_TABLE} "
                    "WHERE scope=$1 AND status != 'deleted' AND superseded_by IS NULL",
                    scope,
                )
        return report

    async def link(self, src_id: str, dst_id: str, *, relation: str, scope: str | None = None) -> None:
        """Edges inherit the src memory's scope so RLS covers them too.

        The MemoryStore contract doesn't carry scope on link(), so callers that
        have it should pass it; without it we look the src row up, which under
        forced RLS returns nothing — the edge is then skipped rather than
        written cross-tenant. dst is verified IN SCOPE inside the same scoped
        transaction (review finding: no cross-tenant edge targets, no FK
        existence oracle). (No production caller links yet.)
        """
        pool = await self._ensure()
        async with pool.acquire() as con:
            async with con.transaction():
                if scope is None:
                    # Best-effort scope discovery; empty under forced RLS.
                    src = await con.fetchrow(f"SELECT scope FROM {_TABLE} WHERE id=$1", src_id)
                    if src is None:
                        return
                    scope = src["scope"]
                scope = self._check_scope(scope)
                await self._scoped(con, scope)
                ok = await con.fetchrow(
                    f"SELECT 1 FROM {_TABLE} WHERE id=$1 AND scope=$2", src_id, scope
                )
                dst_ok = await con.fetchrow(
                    f"SELECT 1 FROM {_TABLE} WHERE id=$1 AND scope=$2", dst_id, scope
                )
                if ok is None or dst_ok is None:
                    return
                await con.execute(
                    f"INSERT INTO {_EDGES} (scope, src, dst, relation) VALUES ($1,$2,$3,$4)",
                    scope, src_id, dst_id, relation,
                )

    async def close(self) -> None:
        loop = asyncio.get_running_loop()
        pool = self._pools.pop(loop, None)
        self._locks.pop(loop, None)
        if pool is not None:
            await pool.close()
