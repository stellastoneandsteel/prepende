"""TenantBrainStore — per-tenant BYO-brain routing config (non-secret).

One row per tenant scope in public.engram_tenant_brain (migration 027). Holds
ONLY non-secret routing metadata (mode / provider / model / base / status); the
key itself is AEAD ciphertext in engram_kernel_secrets via SecretStore.

Reuses the same tenant-isolation machinery as memory/postgres_store.py and
memory/secret_store.py: per-event-loop asyncpg pools, the static _check_scope
guard, and the transaction-local set_config('app.engram_scope') GUC against the
FORCED RLS policy. asyncpg is imported lazily (optional dep).
"""

from __future__ import annotations

import asyncio
from typing import Any

_TABLE = "public.engram_tenant_brain"

_BOOTSTRAP = """
create table if not exists public.engram_tenant_brain (
  scope text primary key,
  mode text not null default 'shared',
  provider text,
  model_id text,
  base_url text,
  secret_purpose text default 'byo:model',
  external_host text,
  status text not null default 'configured',
  last_error text,
  last_verified_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz
);
"""

_FIELDS = ("mode", "provider", "model_id", "base_url", "secret_purpose",
           "external_host", "status", "last_error", "last_verified_at")


class TenantBrainStore:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self._pools: dict[Any, Any] = {}
        self._locks: dict[Any, asyncio.Lock] = {}

    @staticmethod
    def _check_scope(scope: str) -> str:
        scope = (scope or "").strip()
        if not scope:
            raise ValueError("tenant scope must be a non-empty slug")
        return scope

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
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema='public' AND table_name='engram_tenant_brain'"
                )
                if exists is None:
                    raise RuntimeError(
                        "engram_tenant_brain is missing and this role cannot create it — "
                        "apply supabase/migrations/027_engram_tenant_brain.sql as admin first")
            self._pools[loop] = pool
            return pool

    @staticmethod
    async def _scoped(con, scope: str) -> None:
        await con.execute("SELECT set_config('app.engram_scope', $1, true)", scope)

    async def get(self, scope: str) -> dict[str, Any] | None:
        scope = self._check_scope(scope)
        pool = await self._ensure()
        async with pool.acquire() as con:
            async with con.transaction():
                await self._scoped(con, scope)
                row = await con.fetchrow(
                    f"select scope, mode, provider, model_id, base_url, secret_purpose, "
                    f"external_host, status, last_error, last_verified_at from {_TABLE} where scope = $1",
                    scope)
        return dict(row) if row else None

    async def set(self, scope: str, **fields: Any) -> None:
        """Upsert the routing config for `scope`. Only known _FIELDS are written."""
        scope = self._check_scope(scope)
        cols = [f for f in _FIELDS if f in fields]
        vals = [fields[c] for c in cols]
        pool = await self._ensure()
        async with pool.acquire() as con:
            async with con.transaction():
                await self._scoped(con, scope)
                if cols:
                    placeholders = ", ".join(f"${i + 2}" for i in range(len(cols)))
                    set_clause = ", ".join(f"{c} = excluded.{c}" for c in cols)
                    await con.execute(
                        f"insert into {_TABLE} (scope, {', '.join(cols)}, updated_at) "
                        f"values ($1, {placeholders}, now()) "
                        f"on conflict (scope) do update set {set_clause}, updated_at = now()",
                        scope, *vals)
                else:
                    await con.execute(
                        f"insert into {_TABLE} (scope, updated_at) values ($1, now()) "
                        f"on conflict (scope) do update set updated_at = now()", scope)

    async def delete(self, scope: str) -> bool:
        scope = self._check_scope(scope)
        pool = await self._ensure()
        async with pool.acquire() as con:
            async with con.transaction():
                await self._scoped(con, scope)
                res = await con.execute(f"delete from {_TABLE} where scope = $1", scope)
        return str(res).split()[-1] != "0"

    async def close(self) -> None:
        loop = asyncio.get_running_loop()
        pool = self._pools.pop(loop, None)
        self._locks.pop(loop, None)
        if pool is not None:
            await pool.close()
