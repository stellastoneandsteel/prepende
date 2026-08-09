"""Scoped, expiring evidence for connector readiness.

Static configuration can only produce ``configured``.  ``verified`` requires a
fresh receipt from a connector-defined, non-mutating probe in the exact same
tenant and workspace.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from typing import Any

from kernel.core.scope import ScopeIdentity
from prepende_brain.private_fs import prepare_private_sqlite


DEFAULT_READINESS_TTL_SECONDS = 300.0


class ConnectorReadinessStore:
    def __init__(self, path: str = "./.engram/connector_readiness.db") -> None:
        self.path = prepare_private_sqlite(path)
        with self._conn() as c:
            c.execute(
                """CREATE TABLE IF NOT EXISTS connector_readiness_receipts (
                    id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    connector TEXT NOT NULL,
                    connector_version TEXT NOT NULL,
                    probe_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    evidence TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL
                )"""
            )
            c.execute(
                "CREATE INDEX IF NOT EXISTS idx_connector_readiness_scope "
                "ON connector_readiness_receipts(tenant_id, workspace_id, connector, created_at DESC)"
            )

    def _conn(self) -> sqlite3.Connection:
        prepare_private_sqlite(self.path)
        conn = sqlite3.connect(self.path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        prepare_private_sqlite(self.path)
        return conn

    @staticmethod
    def _receipt(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        out = dict(row)
        out["tenantId"] = out.pop("tenant_id")
        out["workspaceId"] = out.pop("workspace_id")
        out["connectorVersion"] = out.pop("connector_version")
        out["probeType"] = out.pop("probe_type")
        out["createdAt"] = out.pop("created_at")
        out["expiresAt"] = out.pop("expires_at")
        out["evidence"] = json.loads(out["evidence"] or "{}")
        out["expired"] = out["expiresAt"] <= time.time()
        return out

    def record(
        self,
        scope: ScopeIdentity,
        *,
        connector: str,
        connector_version: str,
        probe_type: str,
        ok: bool,
        reason: str = "",
        evidence: dict[str, Any] | None = None,
        ttl_seconds: float = DEFAULT_READINESS_TTL_SECONDS,
    ) -> dict[str, Any]:
        if ttl_seconds <= 0:
            raise ValueError("readiness ttl_seconds must be greater than zero")
        receipt_id = f"crr_{uuid.uuid4().hex[:16]}"
        now = time.time()
        status = "verified" if ok else "failed"
        with self._conn() as c:
            c.execute(
                "INSERT INTO connector_readiness_receipts VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    receipt_id, scope.tenant_id, scope.workspace_id, connector,
                    connector_version or "unknown", probe_type or "read_only",
                    status, (reason or "")[:500], json.dumps(evidence or {}, sort_keys=True),
                    now, now + ttl_seconds,
                ),
            )
            row = c.execute(
                "SELECT * FROM connector_readiness_receipts WHERE id=?", (receipt_id,)
            ).fetchone()
        return self._receipt(row)  # type: ignore[return-value]

    def latest(self, scope: ScopeIdentity, connector: str) -> dict[str, Any] | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM connector_readiness_receipts WHERE tenant_id=? AND workspace_id=? "
                "AND connector=? ORDER BY created_at DESC LIMIT 1",
                (scope.tenant_id, scope.workspace_id, connector),
            ).fetchone()
        return self._receipt(row)

    def state(
        self, scope: ScopeIdentity, connector: str, *, configured: bool
    ) -> dict[str, Any]:
        receipt = self.latest(scope, connector)
        current = bool(receipt and not receipt["expired"])
        if current:
            status = receipt["status"]
        else:
            status = "configured" if configured else "unknown"
        evidence = receipt.get("evidence", {}) if current and receipt else {}
        authenticated = evidence.get("authenticated")
        authentication = str(evidence.get("authentication") or "not_checked")
        verified = status == "verified"
        # Older and generic adapters did not emit the operational field. Preserve
        # their prior semantics while allowing concrete adapters to say that a
        # health/auth probe did not prove the actual capability.
        operational = verified and bool(evidence.get("operational", True))
        return {
            "status": status,
            "configured": configured,
            "authenticated": authenticated,
            "authentication": authentication,
            "verified": verified,
            "operational": operational,
            "receipt": receipt,
        }
