"""PostgresApprovalStore — the per-action approval ledger on Postgres / Supabase,
drop-in for ApprovalStore (kernel/core/approvals.py) at full parity.

Same API (stage / get / list / decide / record_execution), same id format
(apr_<hex12>), same receipt shape, same atomic conditional-UPDATE decisions
(replay-safe, at-most-once execution), and the same lazy expiry. Backed by
`public.engram_kernel_approvals` (DDL + RLS in supabase/migrations/020 + 021).

Tenant isolation mirrors PostgresMemoryStore: every statement filters by scope in
WHERE (app-level, always on) AND runs in a transaction that sets the
transaction-local `app.engram_scope` GUC the FORCED RLS policy checks. Production
runs as the non-BYPASSRLS `engram_brain` role; `_ensure` bootstraps the bare table
on a fresh local postgres and merely VERIFIES on a migrated database.

asyncpg is OPTIONAL (kernel.core.approvals.build_approval_store falls back to sqlite
if it's missing/unreachable). Pools are keyed by running loop.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import uuid
from typing import Any

from kernel.core.approvals import DECIDABLE, DEFAULT_TTL_SECONDS

_TABLE = "public.engram_kernel_approvals"

_BOOTSTRAP = f"""
CREATE TABLE IF NOT EXISTS {_TABLE} (
    id           text PRIMARY KEY,
    scope        text NOT NULL CHECK (scope <> ''),
    workflow     text NOT NULL,
    params       jsonb NOT NULL DEFAULT '{{}}'::jsonb,
    reason       text NOT NULL DEFAULT '',
    requested_by text NOT NULL DEFAULT '',
    status       text NOT NULL DEFAULT 'pending'
                   CHECK (status IN ('pending','approved','rejected','expired','executed','execution_failed')),
    created_at   timestamptz NOT NULL DEFAULT now(),
    expires_at   timestamptz,
    decided_by   text,
    decided_at   timestamptz,
    executed_at  timestamptz,
    result       jsonb
);
CREATE INDEX IF NOT EXISTS engram_kernel_approvals_scope_status_idx
    ON {_TABLE} (scope, status, created_at DESC);
"""


def _iso(dt: Any) -> str | None:
    if dt is None:
        return None
    if isinstance(dt, datetime.datetime):
        return dt.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return str(dt)


class PostgresApprovalStore:
    name = "postgres"

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self._pools: dict[Any, Any] = {}
        self._locks: dict[Any, asyncio.Lock] = {}

    @staticmethod
    def _check_scope(scope: str) -> str:
        scope = (scope or "").strip()
        if not scope:
            raise ValueError("approval scope must be a non-empty tenant slug")
        return scope

    @staticmethod
    def _meta(raw: Any) -> Any:
        if raw is None or isinstance(raw, (dict, list)):
            return raw
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            return raw

    def _receipt(self, r: Any) -> dict[str, Any]:
        out = {
            "id": r["id"],
            "tenantId": r["scope"],
            "workflow": r["workflow"],
            "params": self._meta(r["params"]) or {},
            "reason": r["reason"],
            "requestedBy": r["requested_by"],
            "status": r["status"],
            "createdAt": _iso(r["created_at"]),
            "expiresAt": _iso(r["expires_at"]),
            "decidedBy": r["decided_by"],
            "decidedAt": _iso(r["decided_at"]),
            "executedAt": _iso(r["executed_at"]),
        }
        if r["result"] is not None:
            res = self._meta(r["result"])
            out["result"] = res if isinstance(res, (dict, list)) else {"raw": str(res)[:2000]}
        return out

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
                try:
                    await con.execute(_BOOTSTRAP)
                except Exception:
                    pass
                exists = await con.fetchrow(
                    "SELECT 1 FROM information_schema.tables WHERE table_schema='public' "
                    "AND table_name='engram_kernel_approvals'")
                if exists is None:
                    raise RuntimeError(
                        "engram_kernel_approvals is missing and this role cannot create it — "
                        "apply supabase/migrations/020_engram_kernel_queues.sql as admin first")
            self._pools[loop] = pool
            return pool

    @staticmethod
    async def _scoped(con, scope: str) -> None:
        await con.execute("SELECT set_config('app.engram_scope', $1, true)", scope)

    @staticmethod
    async def _expire_due(con, scope: str) -> None:
        """Lazy expiry: pending rows past expires_at flip on any read/decide."""
        await con.execute(
            f"UPDATE {_TABLE} SET status='expired' "
            "WHERE scope=$1 AND status='pending' AND expires_at IS NOT NULL AND expires_at < now()",
            scope)

    async def stage(self, *, scope: str, workflow: str, params: dict[str, Any] | None = None,
                    reason: str = "", requested_by: str = "",
                    ttl_seconds: float = DEFAULT_TTL_SECONDS) -> dict[str, Any]:
        scope = self._check_scope(scope)
        aid = f"apr_{uuid.uuid4().hex[:12]}"
        expires = (datetime.datetime.now(datetime.timezone.utc)
                   + datetime.timedelta(seconds=ttl_seconds)) if ttl_seconds else None
        pool = await self._ensure()
        async with pool.acquire() as con:
            async with con.transaction():
                await self._scoped(con, scope)
                row = await con.fetchrow(
                    f"INSERT INTO {_TABLE} (id, scope, workflow, params, reason, requested_by, "
                    "status, expires_at) VALUES ($1,$2,$3,$4::jsonb,$5,$6,'pending',$7) RETURNING *",
                    aid, scope, workflow, json.dumps(params or {}), reason, requested_by, expires)
        return self._receipt(row)

    async def get(self, approval_id: str, *, scope: str) -> dict[str, Any] | None:
        scope = self._check_scope(scope)
        pool = await self._ensure()
        async with pool.acquire() as con:
            async with con.transaction():
                await self._scoped(con, scope)
                await self._expire_due(con, scope)
                row = await con.fetchrow(
                    f"SELECT * FROM {_TABLE} WHERE id=$1 AND scope=$2", approval_id, scope)
        return self._receipt(row) if row else None

    async def list(self, *, scope: str, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        scope = self._check_scope(scope)
        limit = max(1, min(int(limit), 200))
        pool = await self._ensure()
        async with pool.acquire() as con:
            async with con.transaction():
                await self._scoped(con, scope)
                await self._expire_due(con, scope)
                if status:
                    rows = await con.fetch(
                        f"SELECT * FROM {_TABLE} WHERE scope=$1 AND status=$2 "
                        "ORDER BY created_at DESC LIMIT $3", scope, status, limit)
                else:
                    rows = await con.fetch(
                        f"SELECT * FROM {_TABLE} WHERE scope=$1 "
                        "ORDER BY created_at DESC LIMIT $2", scope, limit)
        return [self._receipt(r) for r in rows]

    async def decide(self, approval_id: str, *, scope: str, decision: str,
                     decided_by: str = "") -> tuple[dict[str, Any] | None, str | None]:
        """Atomically decide a PENDING approval (replay-safe). Returns (receipt, None)
        on a winning transition, else (current_receipt_or_None, error)."""
        if decision not in DECIDABLE:
            return None, "bad_decision"
        scope = self._check_scope(scope)
        new_status = "approved" if decision == "approve" else "rejected"
        pool = await self._ensure()
        async with pool.acquire() as con:
            async with con.transaction():
                await self._scoped(con, scope)
                await self._expire_due(con, scope)
                claimed = await con.execute(
                    f"UPDATE {_TABLE} SET status=$1, decided_by=$2, decided_at=now() "
                    "WHERE id=$3 AND scope=$4 AND status='pending'",
                    new_status, decided_by, approval_id, scope)
                row = await con.fetchrow(
                    f"SELECT * FROM {_TABLE} WHERE id=$1 AND scope=$2", approval_id, scope)
        if row is None:
            return None, "not_found"
        if claimed.endswith(" 0"):
            return self._receipt(row), "not_pending"
        return self._receipt(row), None

    async def record_execution(self, approval_id: str, *, scope: str, ok: bool,
                               result: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """Attach the execution outcome to an APPROVED row (one-way, once)."""
        scope = self._check_scope(scope)
        pool = await self._ensure()
        async with pool.acquire() as con:
            async with con.transaction():
                await self._scoped(con, scope)
                await con.execute(
                    f"UPDATE {_TABLE} SET status=$1, executed_at=now(), result=$2::jsonb "
                    "WHERE id=$3 AND scope=$4 AND status='approved'",
                    "executed" if ok else "execution_failed", json.dumps(result or {}),
                    approval_id, scope)
                row = await con.fetchrow(
                    f"SELECT * FROM {_TABLE} WHERE id=$1 AND scope=$2", approval_id, scope)
        return self._receipt(row) if row else None

    async def close(self) -> None:
        loop = asyncio.get_running_loop()
        pool = self._pools.pop(loop, None)
        self._locks.pop(loop, None)
        if pool is not None:
            await pool.close()
