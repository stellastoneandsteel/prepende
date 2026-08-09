"""SecretStore — durable, tenant-scoped custody for encrypted BYO-brain secrets.

A sibling of memory/postgres_store.py that reuses the SAME isolation machinery:
per-event-loop asyncpg pools, the static `_check_scope` app-side guard, and the
transaction-local `set_config('app.engram_scope', …, true)` GUC against the
FORCED RLS policy on public.engram_kernel_secrets (migration 026).

It moves OPAQUE ciphertext only — it never encrypts or decrypts (that is
kernel/core/keyvault.py, at the point of use). `describe()` returns metadata
(purpose/provider/fingerprint/createdAt) and NEVER the ciphertext or plaintext.

asyncpg is imported lazily so the stdlib-first core stays dependency-free until a
hosted deploy actually uses Postgres.
"""

from __future__ import annotations

import asyncio
from typing import Any

_TABLE = "public.engram_kernel_secrets"

# Bootstrap DDL for bare/local postgres (mirrors migration 026's table; RLS and
# grants are the migration's job in production, where this role can't run DDL).
_BOOTSTRAP = """
create table if not exists public.engram_kernel_secrets (
  scope text not null,
  purpose text not null,
  provider text,
  ciphertext text not null,
  key_fingerprint text,
  key_version int not null default 1,
  meta jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz,
  primary key (scope, purpose)
);
"""


class SecretStore:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self._pools: dict[Any, Any] = {}
        self._locks: dict[Any, asyncio.Lock] = {}

    @staticmethod
    def _check_scope(scope: str) -> str:
        scope = (scope or "").strip()
        if not scope:
            # An empty scope matches the RLS policy's unset-GUC sentinel — reject
            # it before SQL so the isolation hole can't open from the app side.
            raise ValueError("secret scope must be a non-empty tenant slug")
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
            import asyncpg  # lazy: optional dep
            pool = await asyncpg.create_pool(self.dsn, min_size=1, max_size=5)
            async with pool.acquire() as con:
                try:
                    await con.execute(_BOOTSTRAP)  # local/bare pg; denied (and fine) in prod
                except Exception:
                    pass
                exists = await con.fetchrow(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema='public' AND table_name='engram_kernel_secrets'"
                )
                if exists is None:
                    raise RuntimeError(
                        "engram_kernel_secrets is missing and this role cannot create it — "
                        "apply supabase/migrations/026_engram_kernel_secrets.sql as admin first")
            self._pools[loop] = pool
            return pool

    @staticmethod
    async def _scoped(con, scope: str) -> None:
        """Transaction-local tenant scope for the RLS policy (defense layer 2)."""
        await con.execute("SELECT set_config('app.engram_scope', $1, true)", scope)

    async def put(self, scope: str, purpose: str, ciphertext: str, *,
                  provider: str | None = None, fingerprint: str | None = None,
                  meta: dict[str, Any] | None = None) -> None:
        """Upsert the encrypted secret for (scope, purpose). `ciphertext` is the
        opaque base64 from keyvault.seal — this store never sees plaintext."""
        import json
        scope = self._check_scope(scope)
        purpose = (purpose or "").strip()
        if not purpose:
            raise ValueError("purpose must be non-empty")
        if not (ciphertext or "").strip():
            raise ValueError("ciphertext must be non-empty")
        pool = await self._ensure()
        async with pool.acquire() as con:
            async with con.transaction():
                await self._scoped(con, scope)
                await con.execute(
                    f"""insert into {_TABLE} (scope, purpose, provider, ciphertext, key_fingerprint, meta, updated_at)
                        values ($1, $2, $3, $4, $5, $6::jsonb, now())
                        on conflict (scope, purpose) do update
                          set provider = excluded.provider,
                              ciphertext = excluded.ciphertext,
                              key_fingerprint = excluded.key_fingerprint,
                              meta = excluded.meta,
                              updated_at = now()""",
                    scope, purpose, provider, ciphertext, fingerprint, json.dumps(meta or {}),
                )

    async def get_cipher(self, scope: str, purpose: str) -> str | None:
        """The opaque ciphertext for (scope, purpose), or None. Decryption is the
        caller's job via keyvault.unseal(scope, purpose, ciphertext)."""
        scope = self._check_scope(scope)
        pool = await self._ensure()
        async with pool.acquire() as con:
            async with con.transaction():
                await self._scoped(con, scope)
                row = await con.fetchrow(
                    f"select ciphertext from {_TABLE} where scope = $1 and purpose = $2",
                    scope, (purpose or "").strip(),
                )
        return row["ciphertext"] if row else None

    async def describe(self, scope: str) -> list[dict[str, Any]]:
        """Safe metadata for the settings UI — NEVER ciphertext or plaintext."""
        scope = self._check_scope(scope)
        pool = await self._ensure()
        async with pool.acquire() as con:
            async with con.transaction():
                await self._scoped(con, scope)
                rows = await con.fetch(
                    f"select purpose, provider, key_fingerprint, created_at "
                    f"from {_TABLE} where scope = $1 order by purpose",
                    scope,
                )
        return [{
            "purpose": r["purpose"],
            "provider": r["provider"],
            "fingerprint": r["key_fingerprint"],
            "createdAt": r["created_at"].timestamp() if r["created_at"] else None,
        } for r in rows]

    async def delete(self, scope: str, purpose: str | None = None) -> int:
        """Delete one (scope,purpose) secret, or ALL of a scope's secrets when
        purpose is None (revert tenant to the shared brain). Returns count."""
        scope = self._check_scope(scope)
        pool = await self._ensure()
        async with pool.acquire() as con:
            async with con.transaction():
                await self._scoped(con, scope)
                if purpose:
                    res = await con.execute(
                        f"delete from {_TABLE} where scope = $1 and purpose = $2",
                        scope, purpose.strip())
                else:
                    res = await con.execute(f"delete from {_TABLE} where scope = $1", scope)
        # asyncpg returns e.g. "DELETE 2"
        try:
            return int(str(res).split()[-1])
        except Exception:
            return 0

    async def close(self) -> None:
        loop = asyncio.get_running_loop()
        pool = self._pools.pop(loop, None)
        self._locks.pop(loop, None)
        if pool is not None:
            await pool.close()
