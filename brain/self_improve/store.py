"""Durable, scope-locked ledger for prompt self-improvement.

The prompt files remain the versioned artifacts.  This ledger is the safety
chain around them: run -> candidate -> human approval -> promotion request ->
promotion audit.  Every transition includes an exact tenant/workspace match.
Legacy prompt versions have no candidate row and are therefore readable for
audit/rollback selection but cannot be promoted through this gate.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from typing import Any

from kernel.core.scope import ScopeIdentity
from prepende_brain.private_fs import prepare_private_sqlite


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


class SelfImprovementStore:
    def __init__(self, path: str = "./.engram/self_improvement.db") -> None:
        self.path = prepare_private_sqlite(path)
        with self._conn() as c:
            c.executescript(
                """
                CREATE TABLE IF NOT EXISTS improvement_runs (
                    id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    prompt_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    completed_at REAL,
                    error TEXT
                );
                CREATE TABLE IF NOT EXISTS improvement_candidates (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    prompt_id TEXT NOT NULL,
                    previous_version TEXT,
                    candidate_version TEXT NOT NULL,
                    kind TEXT NOT NULL DEFAULT 'improvement',
                    wins INTEGER,
                    total INTEGER,
                    candidate_better INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'staged',
                    created_at REAL NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES improvement_runs(id)
                );
                CREATE TABLE IF NOT EXISTS improvement_approvals (
                    id TEXT PRIMARY KEY,
                    candidate_id TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    approved_by TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    FOREIGN KEY(candidate_id) REFERENCES improvement_candidates(id)
                );
                CREATE TABLE IF NOT EXISTS improvement_promotion_requests (
                    id TEXT PRIMARY KEY,
                    candidate_id TEXT NOT NULL,
                    approval_id TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    prompt_id TEXT NOT NULL,
                    from_version TEXT,
                    to_version TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at REAL NOT NULL,
                    completed_at REAL,
                    reason TEXT,
                    FOREIGN KEY(candidate_id) REFERENCES improvement_candidates(id),
                    FOREIGN KEY(approval_id) REFERENCES improvement_approvals(id)
                );
                CREATE TABLE IF NOT EXISTS improvement_audit (
                    id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    event TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    detail TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_improvement_candidate_scope
                    ON improvement_candidates(tenant_id, workspace_id, status);
                CREATE INDEX IF NOT EXISTS idx_improvement_audit_scope
                    ON improvement_audit(tenant_id, workspace_id, created_at);
                """
            )

    def _conn(self) -> sqlite3.Connection:
        prepare_private_sqlite(self.path)
        conn = sqlite3.connect(self.path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        prepare_private_sqlite(self.path)
        return conn

    @staticmethod
    def _dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row is not None else None

    def _audit(
        self,
        c: sqlite3.Connection,
        scope: ScopeIdentity,
        event: str,
        entity_id: str,
        detail: dict[str, Any] | None = None,
    ) -> None:
        c.execute(
            "INSERT INTO improvement_audit VALUES (?,?,?,?,?,?,?)",
            (
                _id("sia"), scope.tenant_id, scope.workspace_id, event,
                entity_id, json.dumps(detail or {}, sort_keys=True), time.time(),
            ),
        )

    def start_run(self, scope: ScopeIdentity, prompt_id: str) -> dict[str, Any]:
        run_id = _id("sir")
        now = time.time()
        with self._conn() as c:
            c.execute(
                "INSERT INTO improvement_runs VALUES (?,?,?,?,?,?,?,?)",
                (run_id, scope.tenant_id, scope.workspace_id, prompt_id, "running", now, None, None),
            )
            self._audit(c, scope, "run_started", run_id, {"promptId": prompt_id})
            row = c.execute("SELECT * FROM improvement_runs WHERE id=?", (run_id,)).fetchone()
        return dict(row)

    def finish_run(
        self, run_id: str, scope: ScopeIdentity, *, status: str, error: str = ""
    ) -> dict[str, Any]:
        with self._conn() as c:
            cur = c.execute(
                "UPDATE improvement_runs SET status=?, completed_at=?, error=? "
                "WHERE id=? AND tenant_id=? AND workspace_id=? AND status='running'",
                (status, time.time(), error[:500] or None, run_id, scope.tenant_id, scope.workspace_id),
            )
            if cur.rowcount != 1:
                raise ValueError("self-improvement run scope mismatch or run is not active")
            self._audit(c, scope, "run_finished", run_id, {"status": status, "error": error[:500]})
            row = c.execute("SELECT * FROM improvement_runs WHERE id=?", (run_id,)).fetchone()
        return dict(row)

    def stage_candidate(
        self,
        run_id: str,
        scope: ScopeIdentity,
        *,
        prompt_id: str,
        previous_version: str | None,
        candidate_version: str,
        kind: str = "improvement",
    ) -> dict[str, Any]:
        candidate_id = _id("sic")
        initial_status = "evaluated" if kind == "rollback" else "staged"
        eligible = 1 if kind == "rollback" else 0
        with self._conn() as c:
            run = c.execute(
                "SELECT id FROM improvement_runs WHERE id=? AND tenant_id=? AND workspace_id=? "
                "AND prompt_id=? AND status='running'",
                (run_id, scope.tenant_id, scope.workspace_id, prompt_id),
            ).fetchone()
            if run is None:
                raise ValueError("candidate run scope mismatch or run is not active")
            c.execute(
                "INSERT INTO improvement_candidates "
                "(id,run_id,tenant_id,workspace_id,prompt_id,previous_version,candidate_version,kind,"
                "candidate_better,status,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    candidate_id, run_id, scope.tenant_id, scope.workspace_id,
                    prompt_id, previous_version, candidate_version, kind,
                    eligible, initial_status, time.time(),
                ),
            )
            self._audit(c, scope, "candidate_staged", candidate_id, {
                "runId": run_id, "promptId": prompt_id, "version": candidate_version, "kind": kind,
            })
            row = c.execute("SELECT * FROM improvement_candidates WHERE id=?", (candidate_id,)).fetchone()
        return dict(row)

    def get_candidate(self, candidate_id: str, scope: ScopeIdentity) -> dict[str, Any] | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM improvement_candidates WHERE id=? AND tenant_id=? AND workspace_id=?",
                (candidate_id, scope.tenant_id, scope.workspace_id),
            ).fetchone()
        return self._dict(row)

    def record_evaluation(
        self, candidate_id: str, scope: ScopeIdentity, *, wins: int, total: int
    ) -> dict[str, Any]:
        better = bool(total > 0 and wins > total / 2)
        with self._conn() as c:
            cur = c.execute(
                "UPDATE improvement_candidates SET wins=?, total=?, candidate_better=?, status='evaluated' "
                "WHERE id=? AND tenant_id=? AND workspace_id=? AND status='staged'",
                (wins, total, int(better), candidate_id, scope.tenant_id, scope.workspace_id),
            )
            if cur.rowcount != 1:
                raise ValueError("candidate scope mismatch or candidate is not staged")
            self._audit(c, scope, "candidate_evaluated", candidate_id, {
                "wins": wins, "total": total, "candidateBetter": better,
            })
            row = c.execute("SELECT * FROM improvement_candidates WHERE id=?", (candidate_id,)).fetchone()
        return dict(row)

    def approve_candidate(
        self, candidate_id: str, scope: ScopeIdentity, *, approved_by: str
    ) -> dict[str, Any]:
        actor = (approved_by or "").strip()
        if not actor:
            raise ValueError("approved_by is required for human approval")
        approval_id = _id("sia")
        with self._conn() as c:
            candidate = c.execute(
                "SELECT * FROM improvement_candidates WHERE id=? AND tenant_id=? AND workspace_id=? "
                "AND status='evaluated' AND candidate_better=1",
                (candidate_id, scope.tenant_id, scope.workspace_id),
            ).fetchone()
            if candidate is None:
                raise ValueError("candidate scope mismatch, not evaluated, or did not win")
            c.execute(
                "INSERT INTO improvement_approvals VALUES (?,?,?,?,?,?,?)",
                (
                    approval_id, candidate_id, scope.tenant_id, scope.workspace_id,
                    "approved", actor[:200], time.time(),
                ),
            )
            c.execute(
                "UPDATE improvement_candidates SET status='approved' WHERE id=?",
                (candidate_id,),
            )
            self._audit(c, scope, "candidate_approved", candidate_id, {
                "approvalId": approval_id, "approvedBy": actor[:200],
            })
            row = c.execute("SELECT * FROM improvement_approvals WHERE id=?", (approval_id,)).fetchone()
        return dict(row)

    def request_promotion(
        self,
        candidate_id: str,
        approval_id: str,
        scope: ScopeIdentity,
    ) -> dict[str, Any]:
        request_id = _id("sip")
        with self._conn() as c:
            row = c.execute(
                "SELECT c.* FROM improvement_candidates c "
                "JOIN improvement_approvals a ON a.candidate_id=c.id "
                "WHERE c.id=? AND a.id=? "
                "AND c.tenant_id=? AND c.workspace_id=? "
                "AND a.tenant_id=c.tenant_id AND a.workspace_id=c.workspace_id "
                "AND c.status='approved' AND a.decision='approved'",
                (candidate_id, approval_id, scope.tenant_id, scope.workspace_id),
            ).fetchone()
            if row is None:
                raise ValueError("candidate and approval must exist in the exact same scope")
            c.execute(
                "INSERT INTO improvement_promotion_requests "
                "(id,candidate_id,approval_id,tenant_id,workspace_id,prompt_id,from_version,to_version,status,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    request_id, candidate_id, approval_id, scope.tenant_id, scope.workspace_id,
                    row["prompt_id"], row["previous_version"], row["candidate_version"],
                    "pending", time.time(),
                ),
            )
            self._audit(c, scope, "promotion_requested", request_id, {
                "candidateId": candidate_id, "approvalId": approval_id,
            })
            out = c.execute(
                "SELECT * FROM improvement_promotion_requests WHERE id=?", (request_id,)
            ).fetchone()
        return dict(out)

    def finish_promotion(
        self,
        request_id: str,
        scope: ScopeIdentity,
        *,
        promoted: bool,
        reason: str = "",
    ) -> dict[str, Any]:
        status = "promoted" if promoted else "failed"
        with self._conn() as c:
            request = c.execute(
                "SELECT * FROM improvement_promotion_requests WHERE id=? AND tenant_id=? "
                "AND workspace_id=? AND status='pending'",
                (request_id, scope.tenant_id, scope.workspace_id),
            ).fetchone()
            if request is None:
                raise ValueError("promotion request scope mismatch or request is not pending")
            c.execute(
                "UPDATE improvement_promotion_requests SET status=?, completed_at=?, reason=? WHERE id=?",
                (status, time.time(), reason[:500] or None, request_id),
            )
            if promoted:
                c.execute(
                    "UPDATE improvement_candidates SET status='promoted' WHERE id=?",
                    (request["candidate_id"],),
                )
            self._audit(c, scope, f"promotion_{status}", request_id, {"reason": reason[:500]})
            out = c.execute(
                "SELECT * FROM improvement_promotion_requests WHERE id=?", (request_id,)
            ).fetchone()
        return dict(out)

    def audit(self, scope: ScopeIdentity, *, limit: int = 100) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM improvement_audit WHERE tenant_id=? AND workspace_id=? "
                "ORDER BY created_at ASC LIMIT ?",
                (scope.tenant_id, scope.workspace_id, max(1, min(limit, 500))),
            ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["detail"] = json.loads(item["detail"] or "{}")
            out.append(item)
        return out
