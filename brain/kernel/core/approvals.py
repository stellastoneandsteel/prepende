"""ApprovalStore — the durable per-action approval ledger.

Contract: "approve means it happens." A staged
external action (a workflow run) becomes an approval row; approving that ONE
row is the only thing that lifts dry_run for that ONE action. Nothing here
executes anything — the store is pure state; execution lives with the caller
(interface/v1_api.py), which records its result back onto the row.

Lifecycle (one-way, audited, no overwrites):

    pending ──approve──> approved ──record_execution──> executed | execution_failed
       │
       ├─────reject───> rejected            (nothing ran, provably)
       └───(expiresAt)─> expired            (lazy: flipped on read/decide)

Replay safety: decisions are atomic conditional UPDATEs (... WHERE status =
'pending'), so a second approve of the same id — or a concurrent race — loses
and gets the row's real status back instead of a second execution.

At-most-once, by choice: if the process dies between winning the approve claim
and recording the execution, the row stays 'approved' with no executedAt and
can never be re-approved. That is deliberate — for external actions (an email
that sends) a stuck-visible row the operator re-stages is strictly safer than
any auto-retry that could execute twice. The W4 dashboard surfaces
approved-without-executedAt rows for exactly this reason.

Tenant isolation: every read and write filters by scope. An id from another
tenant behaves exactly like a missing id (no existence leak).

Stdlib sqlite (WAL), same discipline as memory/sqlite_store.py. The public methods
are ASYNC (like memory/candidates) so the Postgres twin
(kernel/core/postgres_approvals.py) is a drop-in under RLS — build_approval_store()
picks Postgres when configured, else this sqlite store. The sqlite bodies stay
synchronous inside the async wrappers (a local file; fast, no event-loop starvation
at alpha scale).
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from typing import Any

from prepende_brain.private_fs import prepare_private_sqlite

DECIDABLE = ("approve", "reject")
TERMINAL = ("rejected", "expired", "executed", "execution_failed")
DEFAULT_TTL_SECONDS = 24 * 3600


def _iso(epoch: float | None) -> str | None:
    if epoch is None:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


class ApprovalStore:
    def __init__(self, path: str = "./.engram/approvals.db") -> None:
        self.path = prepare_private_sqlite(path)
        with self._conn() as c:
            c.execute(
                "CREATE TABLE IF NOT EXISTS approvals ("
                "id TEXT PRIMARY KEY, scope TEXT NOT NULL, workflow TEXT NOT NULL, "
                "params TEXT NOT NULL DEFAULT '{}', reason TEXT NOT NULL DEFAULT '', "
                "requested_by TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'pending', "
                "created_at REAL NOT NULL, expires_at REAL, "
                "decided_by TEXT, decided_at REAL, executed_at REAL, result TEXT)"
            )
            c.execute(
                "CREATE INDEX IF NOT EXISTS approvals_scope_status "
                "ON approvals (scope, status, created_at DESC)"
            )

    def _conn(self) -> sqlite3.Connection:
        prepare_private_sqlite(self.path)
        conn = sqlite3.connect(self.path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=3000")
        prepare_private_sqlite(self.path)
        return conn

    @staticmethod
    def _receipt(r: sqlite3.Row) -> dict[str, Any]:
        out = {
            "id": r["id"],
            "tenantId": r["scope"],
            "workflow": r["workflow"],
            "params": json.loads(r["params"] or "{}"),
            "reason": r["reason"],
            "requestedBy": r["requested_by"],
            "status": r["status"],
            "createdAt": _iso(r["created_at"]),
            "expiresAt": _iso(r["expires_at"]),
            "decidedBy": r["decided_by"],
            "decidedAt": _iso(r["decided_at"]),
            "executedAt": _iso(r["executed_at"]),
        }
        if r["result"]:
            try:
                out["result"] = json.loads(r["result"])
            except ValueError:
                out["result"] = {"raw": r["result"][:2000]}
        return out

    def _expire_due(self, c: sqlite3.Connection, scope: str) -> None:
        """Lazy expiry: pending rows past expires_at flip on any read/decide."""
        c.execute(
            "UPDATE approvals SET status='expired' "
            "WHERE scope=? AND status='pending' AND expires_at IS NOT NULL AND expires_at < ?",
            (scope, time.time()),
        )

    async def stage(
        self,
        *,
        scope: str,
        workflow: str,
        params: dict[str, Any] | None = None,
        reason: str = "",
        requested_by: str = "",
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
    ) -> dict[str, Any]:
        """Persist a pending approval for one staged action. Returns its receipt."""
        aid = f"apr_{uuid.uuid4().hex[:12]}"
        now = time.time()
        with self._conn() as c:
            c.execute(
                "INSERT INTO approvals (id, scope, workflow, params, reason, requested_by, "
                "status, created_at, expires_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (aid, scope, workflow, json.dumps(params or {}), reason, requested_by,
                 "pending", now, now + ttl_seconds if ttl_seconds else None),
            )
            row = c.execute("SELECT * FROM approvals WHERE id=?", (aid,)).fetchone()
        return self._receipt(row)

    async def get(self, approval_id: str, *, scope: str) -> dict[str, Any] | None:
        with self._conn() as c:
            self._expire_due(c, scope)
            row = c.execute(
                "SELECT * FROM approvals WHERE id=? AND scope=?", (approval_id, scope)
            ).fetchone()
        return self._receipt(row) if row else None

    async def list(self, *, scope: str, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 200))
        with self._conn() as c:
            self._expire_due(c, scope)
            if status:
                rows = c.execute(
                    "SELECT * FROM approvals WHERE scope=? AND status=? "
                    "ORDER BY created_at DESC LIMIT ?", (scope, status, limit)).fetchall()
            else:
                rows = c.execute(
                    "SELECT * FROM approvals WHERE scope=? "
                    "ORDER BY created_at DESC LIMIT ?", (scope, limit)).fetchall()
        return [self._receipt(r) for r in rows]

    async def decide(
        self, approval_id: str, *, scope: str, decision: str, decided_by: str = ""
    ) -> tuple[dict[str, Any] | None, str | None]:
        """Atomically decide a PENDING approval.

        Returns (receipt, None) when this call won the transition, or
        (current_receipt_or_None, error) when it didn't:
          error = "not_found"   — no such id in this tenant
          error = "not_pending" — already decided/expired; receipt shows by whom/what
          error = "bad_decision" — decision not in approve|reject
        """
        if decision not in DECIDABLE:
            return None, "bad_decision"
        new_status = "approved" if decision == "approve" else "rejected"
        now = time.time()
        with self._conn() as c:
            self._expire_due(c, scope)
            cur = c.execute(
                "UPDATE approvals SET status=?, decided_by=?, decided_at=? "
                "WHERE id=? AND scope=? AND status='pending'",
                (new_status, decided_by, now, approval_id, scope),
            )
            row = c.execute(
                "SELECT * FROM approvals WHERE id=? AND scope=?", (approval_id, scope)
            ).fetchone()
        if row is None:
            return None, "not_found"
        if cur.rowcount == 0:
            return self._receipt(row), "not_pending"
        return self._receipt(row), None

    async def record_execution(
        self, approval_id: str, *, scope: str, ok: bool, result: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        """Attach the execution outcome to an APPROVED row (one-way, once)."""
        now = time.time()
        with self._conn() as c:
            c.execute(
                "UPDATE approvals SET status=?, executed_at=?, result=? "
                "WHERE id=? AND scope=? AND status='approved'",
                ("executed" if ok else "execution_failed", now,
                 json.dumps(result or {}), approval_id, scope),
            )
            row = c.execute(
                "SELECT * FROM approvals WHERE id=? AND scope=?", (approval_id, scope)
            ).fetchone()
        return self._receipt(row) if row else None


def build_approval_store(sqlite_path: str = "./.engram/approvals.db"):
    """Pick the approval ledger backend: Postgres when configured (shared rows under
    RLS, same decision as memory/factory via memory.candidates._pg_dsn), else the local
    sqlite store. Both expose the same ASYNC interface, so callers are identical."""
    from memory.candidates import _pg_dsn
    dsn = _pg_dsn()
    if dsn:
        from kernel.core.postgres_approvals import PostgresApprovalStore
        return PostgresApprovalStore(dsn)
    return ApprovalStore(sqlite_path)
