"""Durable, tenant-scoped support ticket ledger.

SQLite keeps local first light dependency-free. Hosted Prepende selects the
same Postgres substrate as memory/approvals, with an explicit scope predicate
on every statement and the transaction-local RLS scope set before access.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import os
import sqlite3
import time
from typing import Any

from prepende_brain.private_fs import prepare_private_sqlite


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        return value.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(float(value)))


def _json(value: Any) -> Any:
    if value is None or isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return {}


class SupportStore:
    name = "sqlite"

    def __init__(self, path: str = "./.engram/support_tickets.db") -> None:
        self.path = prepare_private_sqlite(path)
        with self._conn() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS support_tickets (
                    id TEXT PRIMARY KEY,
                    scope TEXT NOT NULL,
                    email TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    description TEXT NOT NULL,
                    page_url TEXT NOT NULL DEFAULT '',
                    category TEXT NOT NULL,
                    risk_tier TEXT NOT NULL,
                    lane TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    diagnostics TEXT NOT NULL DEFAULT '{}',
                    dispatch TEXT NOT NULL DEFAULT '{}',
                    resolution TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS support_tickets_scope_status_idx
                    ON support_tickets(scope, status, created_at DESC);
                """
            )

    def _conn(self) -> sqlite3.Connection:
        prepare_private_sqlite(self.path)
        con = sqlite3.connect(self.path, timeout=5.0)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA busy_timeout=3000")
        prepare_private_sqlite(self.path)
        return con

    @staticmethod
    def _receipt(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "scope": row["scope"],
            "email": row["email"],
            "subject": row["subject"],
            "description": row["description"],
            "pageUrl": row["page_url"],
            "category": row["category"],
            "riskTier": row["risk_tier"],
            "lane": row["lane"],
            "status": row["status"],
            "createdAt": _iso(row["created_at"]),
            "updatedAt": _iso(row["updated_at"]),
            "diagnostics": _json(row["diagnostics"]) or {},
            "dispatch": _json(row["dispatch"]) or {},
            "resolution": _json(row["resolution"]) or {},
        }

    async def create(self, ticket: dict[str, Any]) -> dict[str, Any]:
        now = time.time()
        with self._conn() as con:
            con.execute(
                "INSERT INTO support_tickets "
                "(id,scope,email,subject,description,page_url,category,risk_tier,lane,status,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    ticket["id"], ticket["scope"], ticket["email"], ticket["subject"],
                    ticket["description"], ticket.get("pageUrl", ""), ticket["category"],
                    ticket["riskTier"], ticket["lane"], ticket["status"], now, now,
                ),
            )
        result = await self.get(ticket["id"], scope=ticket["scope"])
        if result is None:
            raise RuntimeError("support ticket insert failed")
        return result

    async def get(self, ticket_id: str, *, scope: str) -> dict[str, Any] | None:
        with self._conn() as con:
            row = con.execute(
                "SELECT * FROM support_tickets WHERE id=? AND scope=?",
                (ticket_id, scope),
            ).fetchone()
        return self._receipt(row) if row else None

    async def update(
        self,
        ticket_id: str,
        *,
        scope: str,
        status: str,
        diagnostics: dict[str, Any] | None = None,
        dispatch: dict[str, Any] | None = None,
        resolution: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        values = [status, time.time()]
        assignments = ["status=?", "updated_at=?"]
        for column, value in (
            ("diagnostics", diagnostics),
            ("dispatch", dispatch),
            ("resolution", resolution),
        ):
            if value is not None:
                assignments.append(f"{column}=?")
                values.append(json.dumps(value))
        values.extend((ticket_id, scope))
        with self._conn() as con:
            con.execute(
                f"UPDATE support_tickets SET {', '.join(assignments)} WHERE id=? AND scope=?",
                values,
            )
        return await self.get(ticket_id, scope=scope)

    async def list(self, *, scope: str, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 200))
        with self._conn() as con:
            if status:
                rows = con.execute(
                    "SELECT * FROM support_tickets WHERE scope=? AND status=? ORDER BY created_at DESC LIMIT ?",
                    (scope, status, limit),
                ).fetchall()
            else:
                rows = con.execute(
                    "SELECT * FROM support_tickets WHERE scope=? ORDER BY created_at DESC LIMIT ?",
                    (scope, limit),
                ).fetchall()
        return [self._receipt(row) for row in rows]


_TABLE = "public.prepende_support_tickets"
_BOOTSTRAP = f"""
CREATE TABLE IF NOT EXISTS {_TABLE} (
    id text PRIMARY KEY,
    scope text NOT NULL CHECK (scope <> ''),
    email text NOT NULL,
    subject text NOT NULL,
    description text NOT NULL,
    page_url text NOT NULL DEFAULT '',
    category text NOT NULL,
    risk_tier text NOT NULL,
    lane text NOT NULL,
    status text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    diagnostics jsonb NOT NULL DEFAULT '{{}}'::jsonb,
    dispatch jsonb NOT NULL DEFAULT '{{}}'::jsonb,
    resolution jsonb NOT NULL DEFAULT '{{}}'::jsonb
);
CREATE INDEX IF NOT EXISTS prepende_support_tickets_scope_status_idx
    ON {_TABLE}(scope, status, created_at DESC);
"""


class PostgresSupportStore:
    name = "postgres"

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self._pools: dict[Any, Any] = {}
        self._locks: dict[Any, asyncio.Lock] = {}

    async def _pool(self):
        loop = asyncio.get_running_loop()
        pool = self._pools.get(loop)
        if pool is not None:
            return pool
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
                exists = await con.fetchval(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema='public' AND table_name='prepende_support_tickets'"
                )
                if not exists:
                    raise RuntimeError(
                        "prepende_support_tickets is missing and the runtime role cannot create it; "
                        "apply supabase/migrations/041_prepende_support_tickets.sql"
                    )
            self._pools[loop] = pool
            return pool

    @staticmethod
    async def _scoped(con: Any, scope: str) -> None:
        scope = (scope or "").strip()
        if not scope:
            raise ValueError("support scope must be non-empty")
        await con.execute("SELECT set_config('app.engram_scope', $1, true)", scope)

    @staticmethod
    def _receipt(row: Any) -> dict[str, Any]:
        return {
            "id": row["id"], "scope": row["scope"], "email": row["email"],
            "subject": row["subject"], "description": row["description"],
            "pageUrl": row["page_url"], "category": row["category"],
            "riskTier": row["risk_tier"], "lane": row["lane"], "status": row["status"],
            "createdAt": _iso(row["created_at"]), "updatedAt": _iso(row["updated_at"]),
            "diagnostics": _json(row["diagnostics"]) or {},
            "dispatch": _json(row["dispatch"]) or {},
            "resolution": _json(row["resolution"]) or {},
        }

    async def create(self, ticket: dict[str, Any]) -> dict[str, Any]:
        pool = await self._pool()
        async with pool.acquire() as con:
            async with con.transaction():
                await self._scoped(con, ticket["scope"])
                row = await con.fetchrow(
                    f"INSERT INTO {_TABLE} "
                    "(id,scope,email,subject,description,page_url,category,risk_tier,lane,status) "
                    "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10) RETURNING *",
                    ticket["id"], ticket["scope"], ticket["email"], ticket["subject"],
                    ticket["description"], ticket.get("pageUrl", ""), ticket["category"],
                    ticket["riskTier"], ticket["lane"], ticket["status"],
                )
        return self._receipt(row)

    async def get(self, ticket_id: str, *, scope: str) -> dict[str, Any] | None:
        pool = await self._pool()
        async with pool.acquire() as con:
            async with con.transaction():
                await self._scoped(con, scope)
                row = await con.fetchrow(
                    f"SELECT * FROM {_TABLE} WHERE id=$1 AND scope=$2", ticket_id, scope
                )
        return self._receipt(row) if row else None

    async def update(
        self,
        ticket_id: str,
        *,
        scope: str,
        status: str,
        diagnostics: dict[str, Any] | None = None,
        dispatch: dict[str, Any] | None = None,
        resolution: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        pool = await self._pool()
        async with pool.acquire() as con:
            async with con.transaction():
                await self._scoped(con, scope)
                row = await con.fetchrow(
                    f"UPDATE {_TABLE} SET status=$1, updated_at=now(), "
                    "diagnostics=COALESCE($2::jsonb, diagnostics), "
                    "dispatch=COALESCE($3::jsonb, dispatch), "
                    "resolution=COALESCE($4::jsonb, resolution) "
                    "WHERE id=$5 AND scope=$6 RETURNING *",
                    status,
                    json.dumps(diagnostics) if diagnostics is not None else None,
                    json.dumps(dispatch) if dispatch is not None else None,
                    json.dumps(resolution) if resolution is not None else None,
                    ticket_id,
                    scope,
                )
        return self._receipt(row) if row else None

    async def list(self, *, scope: str, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 200))
        pool = await self._pool()
        async with pool.acquire() as con:
            async with con.transaction():
                await self._scoped(con, scope)
                if status:
                    rows = await con.fetch(
                        f"SELECT * FROM {_TABLE} WHERE scope=$1 AND status=$2 "
                        "ORDER BY created_at DESC LIMIT $3", scope, status, limit
                    )
                else:
                    rows = await con.fetch(
                        f"SELECT * FROM {_TABLE} WHERE scope=$1 ORDER BY created_at DESC LIMIT $2",
                        scope, limit,
                    )
        return [self._receipt(row) for row in rows]


_default: Any = None


def default_support_store() -> Any:
    global _default
    if _default is not None:
        return _default
    from memory.candidates import _pg_dsn
    dsn = _pg_dsn()
    if dsn:
        _default = PostgresSupportStore(dsn)
    else:
        path = os.environ.get("PREPENDE_SUPPORT_DB", "./.engram/support_tickets.db")
        _default = SupportStore(path)
    return _default
