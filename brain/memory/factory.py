"""build_memory — pick the MemoryStore from config. Postgres if configured, else sqlite.

MEMORY_BACKEND: auto | sqlite | postgres
  - auto (default): Postgres if DATABASE_URL is a postgres URL AND asyncpg is
    installed; otherwise sqlite. Never breaks startup — always falls back.
  - postgres: force Postgres (errors clearly if DATABASE_URL/asyncpg missing).
  - sqlite: force the local file store.
"""

from __future__ import annotations

import socket
import sys
import urllib.parse

from kernel.contracts import MemoryStore
from kernel.core.config import Config
from memory.sqlite_store import SqliteMemoryStore


def _is_pg(url: str) -> bool:
    return url.startswith("postgres://") or url.startswith("postgresql://")


def _reachable(url: str, timeout: float = 1.5) -> bool:
    """Cheap TCP preflight so auto mode never hands back a store whose first
    write will fail (e.g. a stale localhost DATABASE_URL). Forced
    MEMORY_BACKEND=postgres skips this — explicit choice fails loudly instead."""
    try:
        parsed = urllib.parse.urlparse(url)
        host = parsed.hostname or "localhost"
        port = parsed.port or 5432
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def build_memory(cfg: Config) -> MemoryStore:
    backend = cfg.memory_backend

    def _pg():
        from memory.postgres_store import PostgresMemoryStore
        return PostgresMemoryStore(cfg.database_url)

    if backend == "postgres":
        if not _is_pg(cfg.database_url):
            raise RuntimeError("MEMORY_BACKEND=postgres but DATABASE_URL is not a postgres URL.")
        import asyncpg  # noqa: F401  forced backend fails HERE, not on first use
        return _pg()

    if backend == "sqlite":
        return SqliteMemoryStore(cfg.memory_db)

    # auto
    if _is_pg(cfg.database_url):
        try:
            import asyncpg  # noqa: F401  (presence check)
        except ImportError:
            # DATABASE_URL set but driver missing -> degrade to sqlite, don't
            # crash — and never silently (same bar as the unreachable case).
            print(
                "engram memory: DATABASE_URL is postgres but asyncpg is not installed — "
                "auto backend falling back to sqlite (pip install asyncpg).",
                file=sys.stderr,
            )
            return SqliteMemoryStore(cfg.memory_db)
        if _reachable(cfg.database_url):
            return _pg()
        # Degrading is never silent: durable writes are the whole point of
        # postgres, so the operator must see that they're landing in sqlite.
        print(
            "engram memory: DATABASE_URL unreachable — auto backend falling back to "
            "sqlite (set MEMORY_BACKEND=postgres to fail hard instead).",
            file=sys.stderr,
        )
    return SqliteMemoryStore(cfg.memory_db)
