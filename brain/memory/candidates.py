"""CandidateQueue — the durable half of the Assess gate.

Staged memory candidates (inferred or derived facts) persist here, OUTSIDE the
MemoryStore, so they can never leak into recall. Promotion is explicit:
approve() writes the candidate into the MemoryStore with provenance (and the
ASSESS scores, when staged with them) and marks it approved; reject() closes
it with a reason. Both return receipts.

Review states (W3): pending -> approved | rejected | redacted | deferred.
  approved — graduated to durable memory; memoryId links the write.
  rejected — closed with a reason; never recallable.
  redacted — closed AND the content is scrubbed from the row (privacy review:
             the fact must not survive even in the audit trail).
  deferred — punted with a reason; still decidable later (the one reopenable
             state — approve/reject/redact accept pending OR deferred).

Every surface stages into the same queue (MCP memory_propose, the Actions
bridge /memory/propose, GoalLoop ASSESS candidates), so the approval lane is
one queue per tenant scope regardless of cockpit. Stdlib-only (sqlite3).
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import time
import uuid
from typing import Any

from prepende_brain.private_fs import prepare_private_sqlite

# Provenance keys a connector may stage on a candidate; copied verbatim into the
# durable memory's metadata on approve so a promoted fact's origin stays auditable.
_PROVENANCE_KEYS = (
    "agent_id",
    "connector",
    "approval_path",
    "content_hash",
    "source_fingerprint",
    "dedupe_key",
    "source_mode",
    "source_url",
    "feed_id",
    "published_at",
    "lens_id",
    "template_id",
    "template_source",
    "template_revision",
    "trigger_feed_id",
    "trigger_feed_mode",
    "trigger_feed_url",
)

_KINDS = ("episodic", "semantic", "procedural")
MAX_CANDIDATE_CONTENT = 2000
_LEGACY_IDENTITY_METADATA_KEYS = (
    "lens_id",
    "source_fingerprint",
    "source_mode",
    "template_id",
    "template_revision",
    "agent_target",
)


def normalize_candidate_content(content: str) -> str:
    """Return the exact text the candidate queue persists."""

    return (content or "").strip()[:MAX_CANDIDATE_CONTENT]


def candidate_content_hash(content: str) -> str:
    """Hash candidate text after applying the queue's persistence bound."""

    normalized = normalize_candidate_content(content)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _normalize_dedupe_key(value: Any) -> str:
    """Normalize a caller-selected candidate identity."""

    selected = str(value).strip()
    if not selected:
        raise ValueError("candidate dedupe_key must be non-empty when supplied")
    if len(selected) > 256:
        raise ValueError("candidate dedupe_key exceeds 256 characters")
    return selected


def _recorded_dedupe_key(metadata: dict[str, Any]) -> str | None:
    """Return a canonical legacy metadata key, or ``None`` when not valid.

    Legacy migration never rewrites metadata. Requiring an already-trimmed JSON
    string keeps a backfilled column byte-equal to the receipt metadata used by
    approval integrity checks.
    """

    value = metadata.get("dedupe_key")
    if not isinstance(value, str) or value != value.strip():
        return None
    try:
        return _normalize_dedupe_key(value)
    except ValueError:
        return None


def _decode_metadata(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, dict):
        return dict(raw)
    try:
        decoded = json.loads(raw or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return decoded if isinstance(decoded, dict) else None


def _legacy_candidate_is_compatible(
    legacy_metadata: dict[str, Any],
    proposed_metadata: dict[str, Any],
    selected_key: str,
) -> bool:
    """Check whether an exact legacy row can safely represent a new identity."""

    if "dedupe_key" in legacy_metadata:
        if _recorded_dedupe_key(legacy_metadata) != selected_key:
            return False
    recorded_hash = legacy_metadata.get("content_hash")
    if recorded_hash is not None and recorded_hash != proposed_metadata["content_hash"]:
        return False
    for field in _LEGACY_IDENTITY_METADATA_KEYS:
        recorded = legacy_metadata.get(field)
        if recorded not in (None, "") and recorded != proposed_metadata.get(field):
            return False
    return True


def _backfill_unambiguous_sqlite_dedupe_keys(connection: sqlite3.Connection) -> None:
    """Backfill only one-to-one legacy metadata identities before indexing."""

    rows = connection.execute(
        "SELECT id, scope, metadata, dedupe_key FROM candidates"
    ).fetchall()
    metadata_by_id: dict[str, dict[str, Any] | None] = {}
    identity_rows: dict[tuple[str, str], set[str]] = {}
    for row in rows:
        candidate_id = str(row["id"])
        scope = str(row["scope"])
        metadata = _decode_metadata(row["metadata"])
        metadata_by_id[candidate_id] = metadata
        column_key = row["dedupe_key"]
        if column_key is not None:
            identity_rows.setdefault((scope, str(column_key)), set()).add(
                candidate_id
            )
        if metadata is not None:
            recorded_key = _recorded_dedupe_key(metadata)
            if recorded_key is not None:
                identity_rows.setdefault((scope, recorded_key), set()).add(
                    candidate_id
                )

    for row in rows:
        if row["dedupe_key"] is not None:
            continue
        candidate_id = str(row["id"])
        metadata = metadata_by_id[candidate_id]
        if metadata is None:
            continue
        recorded_key = _recorded_dedupe_key(metadata)
        if recorded_key is None:
            continue
        identity = (str(row["scope"]), recorded_key)
        if identity_rows.get(identity) != {candidate_id}:
            continue
        connection.execute(
            "UPDATE candidates SET dedupe_key = ? "
            "WHERE id = ? AND dedupe_key IS NULL",
            (recorded_key, candidate_id),
        )


def _candidate_metadata(
    content: str,
    metadata: dict[str, Any] | None,
    *,
    dedupe_key: str | None = None,
) -> tuple[dict[str, Any], str | None]:
    """Normalize queue-owned integrity fields without mutating caller data."""

    normalized = normalize_candidate_content(content)
    prepared = dict(metadata or {})
    prepared["content_hash"] = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    selected_key = dedupe_key if dedupe_key is not None else prepared.get("dedupe_key")
    if selected_key is not None:
        selected_key = _normalize_dedupe_key(selected_key)
        prepared["dedupe_key"] = selected_key
    return prepared, selected_key


class CandidateQueue:
    def __init__(self, path: str = "./.engram/memory_candidates.db") -> None:
        self.path = str(prepare_private_sqlite(path))
        with self._conn() as c:
            # Serialize first-open schema repair. Without an immediate write
            # lock, two processes can both observe the missing column and race
            # the ALTER TABLE.
            c.execute("BEGIN IMMEDIATE")
            c.execute(
                """CREATE TABLE IF NOT EXISTS candidates (
                    id TEXT PRIMARY KEY,
                    scope TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    content TEXT NOT NULL,
                    source TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at REAL NOT NULL,
                    reviewed_at REAL,
                    memory_id TEXT,
                    reason TEXT,
                    metadata TEXT,
                    dedupe_key TEXT
                )"""
            )
            columns = {str(row[1]) for row in c.execute("PRAGMA table_info(candidates)")}
            if "dedupe_key" not in columns:
                c.execute("ALTER TABLE candidates ADD COLUMN dedupe_key TEXT")
            c.execute("CREATE INDEX IF NOT EXISTS idx_cand_scope_status ON candidates(scope, status)")
            _backfill_unambiguous_sqlite_dedupe_keys(c)
            c.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_cand_scope_dedupe_key "
                "ON candidates(scope, dedupe_key) WHERE dedupe_key IS NOT NULL"
            )

    def _conn(self) -> sqlite3.Connection:
        prepare_private_sqlite(self.path)
        conn = sqlite3.connect(self.path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 10000")
        prepare_private_sqlite(self.path)
        return conn

    @staticmethod
    def _check_scope(scope: str) -> str:
        scope = (scope or "").strip()
        if not scope:
            raise ValueError("candidate scope must be a non-empty tenant slug")
        return scope

    @staticmethod
    def _row(r: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": r["id"],
            "scope": r["scope"],
            "kind": r["kind"],
            "content": r["content"],
            "source": r["source"],
            "status": r["status"],
            "createdAt": r["created_at"],
            "reviewedAt": r["reviewed_at"],
            "memoryId": r["memory_id"],
            "reason": r["reason"],
            "metadata": json.loads(r["metadata"]) if r["metadata"] else {},
            "dedupeKey": r["dedupe_key"],
        }

    async def propose(
        self,
        content: str,
        *,
        scope: str,
        kind: str = "semantic",
        source: str = "unknown",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
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
        cand_id = f"cand_{uuid.uuid4().hex[:16]}"
        try:
            with self._conn() as c:
                if selected_key is not None:
                    # Serialize keyed lookup, legacy claiming, and insertion.
                    # The partial unique index remains the final invariant, but
                    # this lock also covers NULL-key rows the index cannot see.
                    c.execute("BEGIN IMMEDIATE")
                    existing = c.execute(
                        "SELECT * FROM candidates "
                        "WHERE scope = ? AND dedupe_key = ?",
                        (scope, selected_key),
                    ).fetchone()
                    if existing is not None:
                        return self._row(existing), False

                    legacy_rows = c.execute(
                        "SELECT * FROM candidates WHERE scope = ? "
                        "AND dedupe_key IS NULL AND content = ? "
                        "AND kind = ? AND source = ? "
                        "ORDER BY created_at ASC, id ASC",
                        (scope, content, kind, source),
                    ).fetchall()
                    for legacy in legacy_rows:
                        legacy_metadata = _decode_metadata(legacy["metadata"])
                        if legacy_metadata is None or not _legacy_candidate_is_compatible(
                            legacy_metadata, prepared_meta, selected_key
                        ):
                            continue
                        if legacy["status"] in {"pending", "deferred"}:
                            # Queue-owned integrity fields may be added to an
                            # undecided legacy receipt so it remains approvable.
                            claimed_metadata = dict(legacy_metadata)
                            claimed_metadata["content_hash"] = prepared_meta[
                                "content_hash"
                            ]
                            claimed_metadata["dedupe_key"] = selected_key
                            claimed = c.execute(
                                "UPDATE candidates SET dedupe_key = ?, metadata = ? "
                                "WHERE id = ? AND dedupe_key IS NULL",
                                (
                                    selected_key,
                                    json.dumps(claimed_metadata),
                                    legacy["id"],
                                ),
                            )
                        else:
                            # Terminal review metadata is immutable audit data.
                            claimed = c.execute(
                                "UPDATE candidates SET dedupe_key = ? "
                                "WHERE id = ? AND dedupe_key IS NULL",
                                (selected_key, legacy["id"]),
                            )
                        if claimed.rowcount != 1:  # pragma: no cover - write lock owns row
                            continue
                        selected = c.execute(
                            "SELECT * FROM candidates WHERE id = ? AND scope = ?",
                            (legacy["id"], scope),
                        ).fetchone()
                        if selected is None:  # pragma: no cover - claimed row must exist
                            raise RuntimeError("claimed legacy candidate was not readable")
                        return self._row(selected), False

                c.execute(
                    "INSERT INTO candidates "
                    "(id, scope, kind, content, source, status, created_at, metadata, dedupe_key) "
                    "VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?)",
                    (
                        cand_id,
                        scope,
                        kind,
                        content,
                        source,
                        time.time(),
                        json.dumps(prepared_meta),
                        selected_key,
                    ),
                )
            candidate = await self.get(cand_id, scope=scope)
            if candidate is None:  # pragma: no cover - committed row must be readable
                raise RuntimeError("candidate insert committed but row was not readable")
            return candidate, True
        except sqlite3.IntegrityError:
            if selected_key is None:
                raise
            with self._conn() as c:
                existing = c.execute(
                    "SELECT * FROM candidates WHERE scope = ? AND dedupe_key = ?",
                    (scope, selected_key),
                ).fetchone()
            if existing is None:
                raise
            return self._row(existing), False

    async def get(self, candidate_id: str, *, scope: str) -> dict[str, Any] | None:
        scope = self._check_scope(scope)
        with self._conn() as c:
            r = c.execute(
                "SELECT * FROM candidates WHERE id = ? AND scope = ?", (candidate_id, scope)
            ).fetchone()
        return self._row(r) if r else None

    async def list_pending(self, *, scope: str, limit: int = 50) -> list[dict[str, Any]]:
        scope = self._check_scope(scope)
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM candidates WHERE scope = ? AND status = 'pending'"
                " ORDER BY created_at ASC LIMIT ?",
                (scope, int(limit)),
            ).fetchall()
        return [self._row(r) for r in rows]

    async def list(self, *, scope: str, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        """The review ledger for one tenant: filter by status, or everything."""
        scope = self._check_scope(scope)
        limit = max(1, min(int(limit), 200))
        with self._conn() as c:
            if status:
                rows = c.execute(
                    "SELECT * FROM candidates WHERE scope = ? AND status = ?"
                    " ORDER BY created_at DESC LIMIT ?", (scope, status, limit)).fetchall()
            else:
                rows = c.execute(
                    "SELECT * FROM candidates WHERE scope = ?"
                    " ORDER BY created_at DESC LIMIT ?", (scope, limit)).fetchall()
        return [self._row(r) for r in rows]

    # pending is decidable; deferred is the one reopenable state.
    _DECIDABLE = ("pending", "deferred")

    async def approve(self, candidate_id: str, *, scope: str, store: Any) -> dict[str, Any] | None:
        """Graduate ONE pending/deferred candidate into the MemoryStore.

        This is the only door from staged to durable; the write carries the
        candidate id (and the ASSESS scores it was staged with) so the
        memory's origin stays auditable.

        The transition is an atomic conditional UPDATE: concurrent decisions
        race for one claim, so durable memory can never be written twice for
        one candidate (and an approve/reject race can't do both). At-most-once
        by choice — if the process dies after the claim but before the write,
        the row shows approved-without-memoryId for the operator instead of
        risking a double write.
        """
        cand = await self.get(candidate_id, scope=scope)
        if cand is None:
            return None
        cand_meta = cand.get("metadata") or {}
        # T14: if staged with a content hash, refuse to promote a row whose content
        # changed since propose (out-of-band tamper). Checked BEFORE the claim so a
        # mismatched candidate stays pending, not stuck approved-without-memory.
        expected_hash = cand_meta.get("content_hash")
        if expected_hash:
            actual_hash = hashlib.sha256(cand["content"].encode("utf-8")).hexdigest()
            if actual_hash != expected_hash:
                raise ValueError(
                    "content_hash mismatch: candidate %s content changed since propose; refusing to promote"
                    % candidate_id)
        if cand.get("dedupeKey") and cand_meta.get("dedupe_key") != cand.get("dedupeKey"):
            raise ValueError(
                "dedupe_key mismatch: candidate %s metadata changed since propose; refusing to promote"
                % candidate_id
            )
        with self._conn() as c:
            cur = c.execute(
                "UPDATE candidates SET status = 'approved', reviewed_at = ?"
                " WHERE id = ? AND scope = ? AND status IN ('pending', 'deferred')",
                (time.time(), candidate_id, scope),
            )
            if cur.rowcount == 0:
                return None  # someone else decided first
        meta = {
            "source": cand["source"],
            "kind": cand["kind"],
            "approval": "assess_approved",
            "candidateId": cand["id"],
        }
        scores = cand_meta.get("scores")
        if isinstance(scores, dict) and scores:
            meta["assessScores"] = scores
        # Carry connector provenance into the durable memory so a promoted fact's
        # origin (which agent/connector proposed it, under what approval path) stays
        # auditable — the operator can reason "should I trust this?".
        for key in _PROVENANCE_KEYS:
            if cand_meta.get(key):
                meta[key] = cand_meta[key]
        # Brain-update drafts (W6) mark the fact they replace: promotion then
        # SUPERSEDES the old memory — temporal validity, never a duplicate.
        # Falls back to a plain write if the target is gone or the store
        # can't supersede; the approval must not be lost over lineage.
        supersedes = (cand.get("metadata") or {}).get("supersedes")
        memory_id = None
        if supersedes and hasattr(store, "supersede"):
            memory_id = await store.supersede(
                str(supersedes), cand["content"], scope=scope, metadata=meta)
        if memory_id is None:
            memory_id = await store.write(cand["content"], scope=scope, metadata=meta)
        with self._conn() as c:
            c.execute(
                "UPDATE candidates SET memory_id = ? WHERE id = ? AND scope = ?",
                (memory_id, candidate_id, scope),
            )
        return await self.get(candidate_id, scope=scope)

    async def reject(self, candidate_id: str, *, scope: str, reason: str = "") -> dict[str, Any] | None:
        return await self._close(candidate_id, scope=scope, status="rejected", reason=reason)

    async def redact(self, candidate_id: str, *, scope: str, reason: str = "") -> dict[str, Any] | None:
        """Close AND scrub: privacy review decided the content must not
        survive in the queue's audit trail either."""
        return await self._close(candidate_id, scope=scope, status="redacted",
                                 reason=reason, scrub_content=True)

    async def defer(self, candidate_id: str, *, scope: str, reason: str = "") -> dict[str, Any] | None:
        """Punt the decision; the candidate stays decidable later."""
        with self._conn() as c:
            cur = c.execute(
                "UPDATE candidates SET status = 'deferred', reviewed_at = ?, reason = ?"
                " WHERE id = ? AND scope = ? AND status = 'pending'",
                (time.time(), (reason or "").strip()[:500], candidate_id, scope),
            )
            if cur.rowcount == 0:
                return None
        return await self.get(candidate_id, scope=scope)

    async def _close(
        self, candidate_id: str, *, scope: str, status: str, reason: str = "",
        scrub_content: bool = False,
    ) -> dict[str, Any] | None:
        sets = "status = ?, reviewed_at = ?, reason = ?"
        params: list[Any] = [status, time.time(), (reason or "").strip()[:500]]
        if scrub_content:
            sets += ", content = '[redacted]', metadata = '{}'"
        params.extend([candidate_id, scope])
        with self._conn() as c:
            cur = c.execute(
                f"UPDATE candidates SET {sets} "
                "WHERE id = ? AND scope = ? AND status IN ('pending', 'deferred')",
                params,
            )
            if cur.rowcount == 0:
                return None
        return await self.get(candidate_id, scope=scope)


_default: Any = None


def _pg_dsn() -> str | None:
    """Return a postgres DSN if the queue should run on Postgres, else None.
    Mirrors memory/factory.build_memory: MEMORY_BACKEND auto|postgres|sqlite, with
    a reachability preflight and LOUD (never silent) degradation to sqlite."""
    backend = (os.environ.get("MEMORY_BACKEND", "auto") or "auto").strip().lower()
    dsn = (os.environ.get("DATABASE_URL", "") or "").strip()
    if backend == "sqlite":
        return None
    from memory.factory import _is_pg, _reachable  # one source of truth for the decision
    if backend == "postgres":
        if not _is_pg(dsn):
            raise RuntimeError("MEMORY_BACKEND=postgres but DATABASE_URL is not a postgres URL.")
        import asyncpg  # noqa: F401  forced backend fails HERE, not on first use
        return dsn
    # auto
    if _is_pg(dsn):
        try:
            import asyncpg  # noqa: F401
        except ImportError:
            print("engram candidates: DATABASE_URL is postgres but asyncpg not installed — "
                  "queue falling back to sqlite (pip install asyncpg).", file=sys.stderr)
            return None
        if _reachable(dsn):
            return dsn
        print("engram candidates: DATABASE_URL unreachable — queue falling back to sqlite "
              "(set MEMORY_BACKEND=postgres to fail hard instead).", file=sys.stderr)
    return None


def default_queue() -> Any:
    """Process-wide Assess queue. Postgres when configured (shared rows with the
    production cockpit, under RLS); otherwise the local sqlite file next to
    MEMORY_DB so tests that isolate MEMORY_DB isolate candidates too. Same async
    API either way (PostgresCandidateQueue is a drop-in)."""
    global _default
    if _default is None:
        dsn = _pg_dsn()
        if dsn:
            from memory.postgres_candidates import PostgresCandidateQueue
            _default = PostgresCandidateQueue(dsn)
        else:
            db = (os.environ.get("MEMORY_DB", "./.engram/memory.db") or "./.engram/memory.db").strip()
            _default = CandidateQueue(os.path.join(os.path.dirname(db) or ".", "memory_candidates.db"))
    return _default
