#!/usr/bin/env python3
"""Smoke: the recovery CLI refuses unknown gates and accepts proven receipts."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from operations.continuity import RECOVERY_GATE_IDS, RECOVERY_SCHEMA_VERSION
from operations.recovery_receipts import (
    GATE_POLICIES,
    build_manifest,
    build_receipt,
    digest_bytes,
    observation_template,
    write_receipt,
)


SCRIPT = ROOT / "scripts" / "verify_prepende_recovery.py"


def iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def run(
    path: Path,
    *,
    scope: str | None = None,
) -> tuple[subprocess.CompletedProcess[str], dict]:
    args = [sys.executable, str(SCRIPT), "--manifest", str(path), "--json"]
    if scope:
        args.extend(["--scope", scope])
    proc = subprocess.run(
        args,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return proc, json.loads(proc.stdout)


def main() -> None:
    now = datetime.now(timezone.utc)
    with tempfile.TemporaryDirectory(prefix="prepende-recovery-verifier-") as temp_dir:
        path = Path(temp_dir) / "manifest.json"
        manifest = {
            "schemaVersion": RECOVERY_SCHEMA_VERSION,
            "scope": "prepende-operations",
            "generatedAt": iso(now),
            "expiresAt": iso(now + timedelta(days=1)),
            "gates": [{"id": gate_id, "status": "unknown", "evidence": []} for gate_id in RECOVERY_GATE_IDS],
        }
        path.write_text(json.dumps(manifest), encoding="utf-8")
        proc, payload = run(path)
        assert proc.returncode == 1, payload
        assert payload["ok"] is False, payload
        assert payload["result"]["gateCounts"]["unknown"] == 10, payload

        receipts_dir = path.parent / "receipts"
        source = b"fixture evidence"
        for gate_id in RECOVERY_GATE_IDS:
            observation = observation_template(gate_id, now=now)
            observation["producer"] = {
                "id": f"fixture-{gate_id}",
                "version": "1",
                "kind": GATE_POLICIES[gate_id]["producerKinds"][0],
            }
            observation["summary"] = f"Fixture proof for {gate_id}."
            observation["checks"] = [
                {"id": check_id, "status": "pass", "detail": "fixture"}
                for check_id in GATE_POLICIES[gate_id]["checks"]
            ]
            observation["artifacts"] = [
                {
                    "id": "fixture",
                    "locator": f"fixture://{gate_id}",
                    "digest": digest_bytes(source),
                    "bytes": len(source),
                }
            ]
            observation["safety"]["isolation"] = "temporary_local"
            receipt = build_receipt(
                observation,
                source_locator=f"fixture://{gate_id}",
                source_digest=digest_bytes(source),
                source_bytes=len(source),
                now=now,
            )
            write_receipt(receipt, receipts_dir)
        _, diagnostics = build_manifest(
            receipts_dir=receipts_dir,
            output_path=path,
            scope="prepende-operations",
            now=now,
        )
        assert diagnostics["invalidReceiptCount"] == 0, diagnostics

        # A newer receipt from another tenant must never replace this scope's
        # selected gate evidence.
        other_scope = observation_template("inventory", now=now)
        other_scope["scope"] = "other-business"
        other_scope["producer"] = {
            "id": "other-scope-fixture",
            "version": "1",
            "kind": GATE_POLICIES["inventory"]["producerKinds"][0],
        }
        other_scope["summary"] = "Other tenant failed its own inventory drill."
        other_scope["checks"] = [
            {"id": check_id, "status": "fail", "detail": "other tenant"}
            for check_id in GATE_POLICIES["inventory"]["checks"]
        ]
        other_scope_receipt = build_receipt(
            other_scope,
            source_locator="fixture://other-business/inventory",
            source_digest=digest_bytes(source),
            source_bytes=len(source),
            now=now,
        )
        write_receipt(other_scope_receipt, receipts_dir)
        scoped_manifest, scoped_diagnostics = build_manifest(
            receipts_dir=receipts_dir,
            output_path=path,
            scope="prepende-operations",
            now=now,
        )
        assert scoped_diagnostics["ignoredOtherScopeCount"] == 1, scoped_diagnostics
        inventory = next(
            gate for gate in scoped_manifest["gates"] if gate["id"] == "inventory"
        )
        assert inventory["status"] == "pass", inventory

        proc, payload = run(path)
        assert proc.returncode == 0, proc.stderr or payload
        assert payload["ok"] is True, payload
        assert payload["result"]["proven"] is True, payload
        assert payload["externalActions"] == [], payload
        assert payload["durableMemoryWrite"] is False, payload

        proc, payload = run(path, scope="other-business")
        assert proc.returncode == 1, payload
        assert "recovery_manifest_scope_mismatch" in payload["result"]["reasons"], payload

        inventory_path = path.parent / inventory["evidence"][0]["receiptPath"]
        tampered = json.loads(inventory_path.read_text(encoding="utf-8"))
        tampered["summary"] = "tampered"
        inventory_path.write_text(json.dumps(tampered), encoding="utf-8")
        proc, payload = run(path)
        assert proc.returncode == 1, payload
        assert "gate_evidence_invalid:inventory" in payload["result"]["reasons"], payload

    print("smoke_recovery_verifier OK")


if __name__ == "__main__":
    main()
