"""RunStore — a durable journal of goal runs (stdlib sqlite, zero infra).

Every goal becomes a recorded run with a status. If the process dies mid-goal,
the run is left as 'running' and is detected as INTERRUPTED on next startup, so
you can resume it. This is the pragmatic, zero-infra implementation of the
DurableExecution idea; the full port (submit/status/cancel on Temporal) is the
scale swap behind the same concept.

Note: solo goals are a single step, so "resume" re-runs the goal. True mid-step
resume (continue a half-finished plan) arrives with the multi-step tactics in
Phase 2 — the journal + checkpoints are the foundation for it.
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any

from prepende_brain.private_fs import prepare_private_sqlite


class RunStore:
    def __init__(self, path: str = "./.engram/runs.db") -> None:
        self.path = prepare_private_sqlite(path)
        with self._conn() as c:
            c.execute(
                "CREATE TABLE IF NOT EXISTS runs ("
                "goal_id TEXT PRIMARY KEY, goal TEXT NOT NULL, status TEXT NOT NULL, "
                "result TEXT, error TEXT, started REAL NOT NULL, updated REAL NOT NULL)"
            )

    def _conn(self) -> sqlite3.Connection:
        prepare_private_sqlite(self.path)
        conn = sqlite3.connect(self.path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")     # concurrent readers + a writer
        conn.execute("PRAGMA busy_timeout=3000")    # wait then error, never hang
        prepare_private_sqlite(self.path)
        return conn

    def start(self, goal_id: str, goal: str) -> None:
        now = time.time()
        with self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO runs (goal_id, goal, status, result, error, started, updated) "
                "VALUES (?,?, 'running', NULL, NULL, ?, ?)",
                (goal_id, goal, now, now),
            )

    def finish(self, goal_id: str, result: str) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE runs SET status='done', result=?, updated=? WHERE goal_id=?",
                ((result or "")[:8000], time.time(), goal_id),
            )

    def fail(self, goal_id: str, error: str) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE runs SET status='failed', error=?, updated=? WHERE goal_id=?",
                (str(error)[:2000], time.time(), goal_id),
            )

    def get(self, goal_id: str) -> dict[str, Any] | None:
        with self._conn() as c:
            r = c.execute("SELECT * FROM runs WHERE goal_id=?", (goal_id,)).fetchone()
            return dict(r) if r else None

    def recent(self, n: int = 10) -> list[dict[str, Any]]:
        with self._conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM runs ORDER BY updated DESC LIMIT ?", (n,)).fetchall()]

    def interrupted(self) -> list[dict[str, Any]]:
        """Runs left 'running' — i.e. a process died mid-goal (crash survivors)."""
        with self._conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM runs WHERE status='running' ORDER BY started DESC").fetchall()]
