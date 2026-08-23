"""PostgresCandidateQueue — the Assess queue on Postgres / Supabase, drop-in for
CandidateQueue (memory/candidates.py) at full parity.

Same async API (propose / get / list_pending / list / approve / reject / redact /
defer), same id format (cand_<hex16>), same at-most-once approve via an atomic
conditional UPDATE, and the same Step-1 connector provenance + content-hash
tamper-evidence on approve. Backed by `public.engram_kernel_memory_candidates`
(DDL + RLS in supabase/migrations/020 + 021).

Tenant isolation, in depth (mirrors PostgresMemoryStore):
  1. every statement filters by scope in WHERE — app-level, always on — and an
     empty/whitespace scope is rejected before any SQL;
  2. every operation runs in a transaction that sets the transaction-local GUC
     `app.engram_scope`, which the FORCED RLS policy checks. Supabase's default
     `postgres` role carries BYPASSRLS, so production runs as `engram_brain`
     (no BYPASSRLS); RLS is then the structural backstop behind the WHERE.
  3. `_ensure` bootstraps the bare table on a fresh local postgres and merely
     VERIFIES on a migrated database where the least-privilege role has no DDL.

asyncpg is OPTIONAL (memory/candidates.default_queue falls back to sqlite if it's
missing/unreachable). Pools are keyed by running loop (asyncio.run-per-request
callers each get their own), same as PostgresMemoryStore.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from typing import Any

from memory.candidates import (  # one source of truth for both
    _KINDS,
    _PROVENANCE_KEYS,
    _candidate_metadata,
    _decode_metadata,
    _legacy_candidate_is_compatible,
    normalize_candidate_content,
)

_TABLE = "public.engram_kernel_memory_candidates"

_BOOTSTRAP = f"""
CREATE TABLE IF NOT EXISTS {_TABLE} (
    id          text PRIMARY KEY,
    scope       text NOT NULL CHECK (scope <> ''),
    kind        text NOT NULL DEFAULT 'semantic' CHECK (kind IN ('episodic','semantic','procedural')),
    content     text NOT NULL,
    source      text NOT NULL DEFAULT 'unknown',
    status      text NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending','approved','rejected','redacted','deferred')),
    created_at  timestamptz NOT NULL DEFAULT now(),
    reviewed_at timestamptz,
    memory_id   text,
    reason      text,
    metadata    jsonb NOT NULL DEFAULT '{{}}'::jsonb,
    dedupe_key  text
);
ALTER TABLE {_TABLE} ADD COLUMN IF NOT EXISTS dedupe_key text;
WITH valid_metadata AS (
    SELECT id, scope, metadata->>'dedupe_key' AS metadata_key
    FROM {_TABLE}
    WHERE jsonb_typeof(metadata->'dedupe_key') = 'string'
      AND metadata->>'dedupe_key' = btrim(metadata->>'dedupe_key')
      AND char_length(metadata->>'dedupe_key') BETWEEN 1 AND 256
), identity_rows AS (
    SELECT id, scope, dedupe_key AS identity_key
    FROM {_TABLE}
    WHERE dedupe_key IS NOT NULL
    UNION
    SELECT id, scope, metadata_key AS identity_key
    FROM valid_metadata
), unambiguous AS (
    SELECT scope, identity_key, min(id) AS id
    FROM identity_rows
    GROUP BY scope, identity_key
    HAVING count(DISTINCT id) = 1
), backfill AS (
    SELECT valid_metadata.id, valid_metadata.scope,
           valid_metadata.metadata_key
    FROM valid_metadata
    JOIN unambiguous
      ON unambiguous.id = valid_metadata.id
     AND unambiguous.scope = valid_metadata.scope
     AND unambiguous.identity_key = valid_metadata.metadata_key
)
UPDATE {_TABLE} AS candidate
SET dedupe_key = backfill.metadata_key
FROM backfill
WHERE candidate.id = backfill.id
  AND candidate.scope = backfill.scope
  AND candidate.dedupe_key IS NULL;
CREATE INDEX IF NOT EXISTS engram_kernel_memory_candidates_scope_status_idx
    ON {_TABLE} (scope, status, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS engram_kernel_memory_candidates_scope_dedupe_key_idx
    ON {_TABLE} (scope, dedupe_key) WHERE dedupe_key IS NOT NULL;
"""

_DECIDABLE = ("pending", "deferred")


class PostgresCandidateQueue:
    name = "postgres"

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self._pools: dict[Any, Any] = {}
        self._locks: dict[Any, asyncio.Lock] = {}

    @staticmethod
    def _check_scope(scope: str) -> str:
        scope = (scope or "").strip()
        if not scope:
            raise ValueError("candidate scope must be a non-empty tenant slug")
        return scope

    @staticmethod
    def _meta(raw: Any) -> dict[str, Any]:
        if isinstance(raw, dict):
            return raw
        return json.loads(raw or "{}")

    def _row(self, r: Any) -> dict[str, Any]:
        return {
            "id": r["id"],
            "scope": r["scope"],
            "kind": r["kind"],
            "content": r["content"],
            "source": r["source"],
            "status": r["status"],
            "createdAt": r["created_at"].timestamp() if r["created_at"] else None,
            "reviewedAt": r["reviewed_at"].timestamp() if r["reviewed_at"] else None,
            "memoryId": r["memory_id"],
            "reason": r["reason"],
            "metadata": self._meta(r["metadata"]),
            "dedupeKey": r["dedupe_key"],
        }

    async def _ensure(self):
        loop = asyncio.get_running_loop()
        pool = self._pools.get(loop)
        if pool is not None:
            return pool
        for stale in [lp for lp in self._pools if lp.is_closed()]:
            self._pools.pop(stale, None)
            self._locks.pop(stale, None)
        lock = self._locks.setdefault(loop, asyncio.Lock())
        async with lock:
            pool = self._pools.get(loop)
            if pool is not None:
                return pool
            import asyncpg
            pool = await asyncpg.create_pool(self.dsn, min_size=1, max_size=5)
            async with pool.acquire() as con:
                try:  # DDL works on bare local pg; denied (correctly) for engram_brain
                    await con.execute(_BOOTSTRAP)
                except Exception:
                    pass
                exists = await con.fetchrow(
                    "SELECT 1 FROM information_schema.tables WHERE table_schema='public' "
                    "AND table_name='engram_kernel_memory_candidates'")
                if exists is None:
                    raise RuntimeError(
                        "engram_kernel_memory_candidates is missing and this role cannot create it "
                        "— apply supabase/migrations/020_engram_kernel_queues.sql as admin first")
                dedupe_column = await con.fetchrow(
                    "SELECT 1 FROM information_schema.columns WHERE table_schema='public' "
                    "AND table_name='engram_kernel_memory_candidates' AND column_name='dedupe_key'")
                if dedupe_column is None:
                    raise RuntimeError(
                        "engram_kernel_memory_candidates.dedupe_key is missing — apply the "
                        "candidate dedupe migration as admin before staging ingestion candidates")
                dedupe_index = await con.fetchrow(
                    "SELECT 1 FROM pg_indexes WHERE schemaname='public' "
                    "AND tablename='engram_kernel_memory_candidates' "
                    "AND indexname='engram_kernel_memory_candidates_scope_dedupe_key_idx'")
                if dedupe_index is None:
                    raise RuntimeError(
                        "candidate dedupe unique index is missing — apply the candidate dedupe "
                        "migration as admin before staging ingestion candidates")
            self._pools[loop] = pool
            return pool

    @staticmethod
    async def _scoped(con, scope: str) -> None:
        await con.execute("SELECT set_config('app.engram_scope', $1, true)", scope)

    async def propose(self, content: str, *, scope: str, kind: str = "semantic",
                      source: str = "unknown", metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        candidate, _created = await self._propose(
            content,
            scope=scope,
            kind=kind,
            source=source,
            metadata=metadata,
        )
        return candidate

    async def propose_unique(
        self,
        content: str,
        *,
        scope: str,
        dedupe_key: str,
        kind: str = "semantic",
        source: str = "unknown",
        metadata: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """Atomically stage once per ``(scope, dedupe_key)`` across all states."""

        return await self._propose(
            content,
            scope=scope,
            kind=kind,
            source=source,
            metadata=metadata,
            dedupe_key=dedupe_key,
        )

    async def _propose(
        self,
        content: str,
        *,
        scope: str,
        kind: str,
        source: str,
        metadata: dict[str, Any] | None,
        dedupe_key: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        scope = self._check_scope(scope)
        content = normalize_candidate_content(content)
        if not content:
            raise ValueError("candidate content is empty")
        kind = kind if kind in _KINDS else "semantic"
        prepared_meta, selected_key = _candidate_metadata(
            content, metadata, dedupe_key=dedupe_key
        )
        cid = f"cand_{uuid.uuid4().hex[:16]}"
        pool = await self._ensure()
        async with pool.acquire() as con:
            async with con.transaction():
                await self._scoped(con, scope)
                if selected_key is not None:
                    # Serialize the NULL-key legacy claim for this canonical
                    # identity. Distinct identities may proceed concurrently;
                    # row locks prevent them from claiming the same legacy row.
                    await con.fetchval(
                        "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
                        f"{scope}\x1f{selected_key}",
                    )
                    existing = await con.fetchrow(
                        f"SELECT * FROM {_TABLE} "
                        "WHERE scope=$1 AND dedupe_key=$2",
                        scope,
                        selected_key,
                    )
                    if existing is not None:
                        return self._row(existing), False

                    legacy_rows = await con.fetch(
                        f"SELECT * FROM {_TABLE} WHERE scope=$1 "
                        "AND dedupe_key IS NULL AND content=$2 "
                        "AND kind=$3 AND source=$4 "
                        "ORDER BY created_at ASC, id ASC FOR UPDATE",
                        scope,
                        content,
                        kind,
                        source,
                    )
                    for legacy in legacy_rows:
                        legacy_metadata = _decode_metadata(legacy["metadata"])
                        if legacy_metadata is None or not _legacy_candidate_is_compatible(
                            legacy_metadata, prepared_meta, selected_key
                        ):
                            continue
                        if legacy["status"] in _DECIDABLE:
                            claimed = await con.fetchrow(
                                f"UPDATE {_TABLE} SET dedupe_key=$1, "
                                "metadata=metadata || jsonb_build_object("
                                "'content_hash', $2::text, 'dedupe_key', $1::text) "
                                "WHERE id=$3 AND scope=$4 AND dedupe_key IS NULL "
                                "RETURNING *",
                                selected_key,
                                prepared_meta["content_hash"],
                                legacy["id"],
                                scope,
                            )
                        else:
                            claimed = await con.fetchrow(
                                f"UPDATE {_TABLE} SET dedupe_key=$1 "
                                "WHERE id=$2 AND scope=$3 AND dedupe_key IS NULL "
                                "RETURNING *",
                                selected_key,
                                legacy["id"],
                                scope,
                            )
                        if claimed is not None:
                            return self._row(claimed), False

                row = await con.fetchrow(
                    f"INSERT INTO {_TABLE} "
                    "(id, scope, kind, content, source, status, metadata, dedupe_key) "
                    "VALUES ($1,$2,$3,$4,$5,'pending',$6::jsonb,$7) "
                    "ON CONFLICT (scope, dedupe_key) WHERE dedupe_key IS NOT NULL "
                    "DO NOTHING RETURNING *",
                    cid,
                    scope,
                    kind,
                    content,
                    source,
                    json.dumps(prepared_meta),
                    selected_key,
                )
                if row is not None:
                    return self._row(row), True
                if selected_key is None:  # pragma: no cover - no matching conflict target
                    raise RuntimeError("candidate insert returned no row without a dedupe key")
                existing = await con.fetchrow(
                    f"SELECT * FROM {_TABLE} WHERE scope=$1 AND dedupe_key=$2",
                    scope,
                    selected_key,
                )
                if existing is None:  # pragma: no cover - unique conflict row must exist
                    raise RuntimeError("candidate dedupe conflict row was not readable")
                return self._row(existing), False

    async def get(self, candidate_id: str, *, scope: str) -> dict[str, Any] | None:
        scope = self._check_scope(scope)
        pool = await self._ensure()
        async with pool.acquire() as con:
            async with con.transaction():
                await self._scoped(con, scope)
                r = await con.fetchrow(
                    f"SELECT * FROM {_TABLE} WHERE id=$1 AND scope=$2", candidate_id, scope)
        return self._row(r) if r else None

    async def list_pending(self, *, scope: str, limit: int = 50) -> list[dict[str, Any]]:
        scope = self._check_scope(scope)
        pool = await self._ensure()
        async with pool.acquire() as con:
            async with con.transaction():
                await self._scoped(con, scope)
                rows = await con.fetch(
                    f"SELECT * FROM {_TABLE} WHERE scope=$1 AND status='pending' "
                    "ORDER BY created_at ASC LIMIT $2", scope, int(limit))
        return [self._row(r) for r in rows]

    async def list(self, *, scope: str, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        scope = self._check_scope(scope)
        limit = max(1, min(int(limit), 200))
        pool = await self._ensure()
        async with pool.acquire() as con:
            async with con.transaction():
                await self._scoped(con, scope)
                if status:
                    rows = await con.fetch(
                        f"SELECT * FROM {_TABLE} WHERE scope=$1 AND status=$2 "
                        "ORDER BY created_at DESC LIMIT $3", scope, status, limit)
                else:
                    rows = await con.fetch(
                        f"SELECT * FROM {_TABLE} WHERE scope=$1 "
                        "ORDER BY created_at DESC LIMIT $2", scope, limit)
        return [self._row(r) for r in rows]

    async def approve(self, candidate_id: str, *, scope: str, store: Any) -> dict[str, Any] | None:
        """Graduate ONE pending/deferred candidate into the MemoryStore — the only
        door from staged to durable. At-most-once via an atomic conditional UPDATE;
        connector provenance + the Assess scores ride onto the durable write, and a
        content_hash mismatch (out-of-band tamper) is REFUSED before the claim."""
        scope = self._check_scope(scope)
        cand = await self.get(candidate_id, scope=scope)
        if cand is None:
            return None
        cand_meta = cand.get("metadata") or {}
        expected = cand_meta.get("content_hash")
        if expected and hashlib.sha256(cand["content"].encode("utf-8")).hexdigest() != expected:
            raise ValueError(
                "content_hash mismatch: candidate %s content changed since propose; refusing to promote"
                % candidate_id)
        if cand.get("dedupeKey") and cand_meta.get("dedupe_key") != cand.get("dedupeKey"):
            raise ValueError(
                "dedupe_key mismatch: candidate %s metadata changed since propose; refusing to promote"
                % candidate_id
            )
        pool = await self._ensure()
        async with pool.acquire() as con:
            async with con.transaction():
                await self._scoped(con, scope)
                claimed = await con.execute(
                    f"UPDATE {_TABLE} SET status='approved', reviewed_at=now() "
                    "WHERE id=$1 AND scope=$2 AND status IN ('pending','deferred')",
                    candidate_id, scope)
        if claimed.endswith(" 0"):
            return None  # someone else decided first
        meta = {"source": cand["source"], "kind": cand["kind"],
                "approval": "assess_approved", "candidateId": cand["id"]}
        scores = cand_meta.get("scores")
        if isinstance(scores, dict) and scores:
            meta["assessScores"] = scores
        for key in _PROVENANCE_KEYS:
            if cand_meta.get(key):
                meta[key] = cand_meta[key]
        supersedes = cand_meta.get("supersedes")
        memory_id = None
        if supersedes and hasattr(store, "supersede"):
            memory_id = await store.supersede(str(supersedes), cand["content"], scope=scope, metadata=meta)
        if memory_id is None:
            memory_id = await store.write(cand["content"], scope=scope, metadata=meta)
        async with pool.acquire() as con:
            async with con.transaction():
                await self._scoped(con, scope)
                await con.execute(
                    f"UPDATE {_TABLE} SET memory_id=$1 WHERE id=$2 AND scope=$3",
                    memory_id, candidate_id, scope)
        return await self.get(candidate_id, scope=scope)

    async def reject(self, candidate_id: str, *, scope: str, reason: str = "") -> dict[str, Any] | None:
        return await self._close(candidate_id, scope=scope, status="rejected", reason=reason)

    async def redact(self, candidate_id: str, *, scope: str, reason: str = "") -> dict[str, Any] | None:
        return await self._close(candidate_id, scope=scope, status="redacted",
                                 reason=reason, scrub_content=True)

    async def defer(self, candidate_id: str, *, scope: str, reason: str = "") -> dict[str, Any] | None:
        scope = self._check_scope(scope)
        pool = await self._ensure()
        async with pool.acquire() as con:
            async with con.transaction():
                await self._scoped(con, scope)
                res = await con.execute(
                    f"UPDATE {_TABLE} SET status='deferred', reviewed_at=now(), reason=$1 "
                    "WHERE id=$2 AND scope=$3 AND status='pending'",
                    (reason or "").strip()[:500], candidate_id, scope)
        if res.endswith(" 0"):
            return None
        return await self.get(candidate_id, scope=scope)

    async def _close(self, candidate_id: str, *, scope: str, status: str, reason: str = "",
                     scrub_content: bool = False) -> dict[str, Any] | None:
        scope = self._check_scope(scope)
        pool = await self._ensure()
        async with pool.acquire() as con:
            async with con.transaction():
                await self._scoped(con, scope)
                if scrub_content:
                    res = await con.execute(
                        f"UPDATE {_TABLE} SET status=$1, reviewed_at=now(), reason=$2, "
                        "content='[redacted]', metadata='{}'::jsonb "
                        "WHERE id=$3 AND scope=$4 AND status IN ('pending','deferred')",
                        status, (reason or "").strip()[:500], candidate_id, scope)
                else:
                    res = await con.execute(
                        f"UPDATE {_TABLE} SET status=$1, reviewed_at=now(), reason=$2 "
                        "WHERE id=$3 AND scope=$4 AND status IN ('pending','deferred')",
                        status, (reason or "").strip()[:500], candidate_id, scope)
        if res.endswith(" 0"):
            return None
        return await self.get(candidate_id, scope=scope)

    async def close(self) -> None:
        loop = asyncio.get_running_loop()
        pool = self._pools.pop(loop, None)
        self._locks.pop(loop, None)
        if pool is not None:
            await pool.close()
