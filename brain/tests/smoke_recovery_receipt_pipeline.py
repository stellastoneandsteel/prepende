#!/usr/bin/env python3
"""Smoke: templates, immutable receipts, and newest-result manifest selection."""

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

from operations.recovery_receipts import (
    GATE_POLICIES,
    build_receipt,
    digest_bytes,
    observation_template,
    write_receipt,
)


SCRIPT = ROOT / "scripts" / "prepende_recovery_receipts.py"


def run(*args: str) -> tuple[subprocess.CompletedProcess[str], dict]:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return proc, json.loads(proc.stdout)


def iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def main() -> None:
    now = datetime.now(timezone.utc)
    with tempfile.TemporaryDirectory(prefix="prepende-recovery-receipts-") as temp_dir:
        temp = Path(temp_dir)
        receipts = temp / "receipts"
        manifest = temp / "recovery-manifest.json"
        artifact = temp / "source-restore-output.json"
        artifact.write_text('{"clone":"ok","build":"ok"}\n', encoding="utf-8")

        proc, template = run("template", "--gate", "source_recovery")
        assert proc.returncode == 0, proc.stderr
        template["observedAt"] = iso(now - timedelta(minutes=2))
        template["expiresAt"] = iso(now + timedelta(days=1))
        template["producer"] = {"id": "source-restore-smoke", "version": "1", "kind": "controlled_drill"}
        template["summary"] = "Fresh isolated clone and build fixture passed."
        template["checks"] = [
            {"id": check["id"], "status": "pass", "detail": "fixture"}
            for check in template["checks"]
        ]
        template["artifacts"] = [{"id": "source-restore-output", "path": str(artifact)}]
        template["safety"]["isolation"] = "temporary_local"
        pass_observation = temp / "source-pass.json"
        pass_observation.write_text(json.dumps(template), encoding="utf-8")

        proc, payload = run("record", "--input", str(pass_observation), "--receipts-dir", str(receipts))
        assert proc.returncode == 0, payload
        assert payload["status"] == "pass", payload

        proc, payload = run(
            "build",
            "--receipts-dir",
            str(receipts),
            "--output",
            str(manifest),
            "--scope",
            "prepende-operations",
            "--dry-run",
        )
        assert proc.returncode == 1, payload
        assert payload["gateCounts"] == {"pass": 1, "fail": 0, "unknown": 9}, payload

        failed = json.loads(json.dumps(template))
        failed["observedAt"] = iso(now - timedelta(minutes=1))
        failed["checks"][1]["status"] = "fail"
        failed["checks"][1]["detail"] = "Expected revision was absent."
        failed_observation = temp / "source-fail.json"
        failed_observation.write_text(json.dumps(failed), encoding="utf-8")
        proc, payload = run("record", "--input", str(failed_observation), "--receipts-dir", str(receipts))
        assert proc.returncode == 0, payload
        assert payload["status"] == "fail", payload

        proc, payload = run(
            "build",
            "--receipts-dir",
            str(receipts),
            "--output",
            str(manifest),
            "--scope",
            "prepende-operations",
            "--dry-run",
        )
        assert proc.returncode == 1, payload
        assert payload["gateCounts"] == {"pass": 0, "fail": 1, "unknown": 9}, payload
        assert payload["diagnostics"]["selected"][0]["status"] == "fail", payload

        proc, payload = run(
            "record-gap",
            "--gate",
            "lost_machine_drill",
            "--scope",
            "prepende-operations",
            "--summary",
            "No replacement host was available for this bounded fixture.",
            "--receipts-dir",
            str(receipts),
        )
        assert proc.returncode == 0, payload
        assert payload["status"] == "fail", payload
        gap_receipt = json.loads(Path(payload["receiptPath"]).read_text(encoding="utf-8"))
        assert gap_receipt["scope"] == "prepende-operations", gap_receipt
        assert all(check["status"] == "fail" for check in gap_receipt["checks"]), gap_receipt

        invalid = json.loads(json.dumps(template))
        invalid["proofClass"] = "responsive_cli"
        invalid_path = temp / "invalid.json"
        invalid_path.write_text(json.dumps(invalid), encoding="utf-8")
        proc, payload = run("record", "--input", str(invalid_path), "--receipts-dir", str(receipts))
        assert proc.returncode == 2, payload
        assert "requires proofClass" in payload["error"], payload

        full_receipts = temp / "full-receipts"
        full_manifest = temp / "full-manifest.json"
        source = b"full fixture evidence"
        for gate_id, policy in GATE_POLICIES.items():
            observation = observation_template(gate_id, now=now)
            observation["producer"] = {
                "id": f"full-fixture-{gate_id}",
                "version": "1",
                "kind": policy["producerKinds"][0],
            }
            observation["summary"] = f"Full fixture proof for {gate_id}."
            observation["checks"] = [
                {"id": check_id, "status": "pass", "detail": "fixture"}
                for check_id in policy["checks"]
            ]
            observation["artifacts"] = [
                {
                    "id": "fixture",
                    "locator": f"fixture://{gate_id}",
                    "digest": digest_bytes(source),
                    "bytes": len(source),
                }
            ]
            receipt = build_receipt(
                observation,
                source_locator=f"fixture://{gate_id}",
                source_digest=digest_bytes(source),
                source_bytes=len(source),
                now=now,
            )
            write_receipt(receipt, full_receipts)
        (full_receipts / "rr_invalid_fixture.json").write_text(
            json.dumps({"scope": "prepende-operations"}),
            encoding="utf-8",
        )
        (full_receipts / "rr_missing_scope_fixture.json").write_text(
            json.dumps({}),
            encoding="utf-8",
        )
        scope_tamper_observation = observation_template("inventory", now=now)
        scope_tamper_observation["producer"] = {
            "id": "scope-tamper-fixture",
            "version": "1",
            "kind": GATE_POLICIES["inventory"]["producerKinds"][0],
        }
        scope_tamper_observation["summary"] = "Fixture whose scope is changed after sealing."
        scope_tamper_observation["checks"] = [
            {"id": check_id, "status": "pass", "detail": "fixture"}
            for check_id in GATE_POLICIES["inventory"]["checks"]
        ]
        scope_tamper_observation["artifacts"] = [
            {
                "id": "fixture",
                "locator": "fixture://scope-tamper",
                "digest": digest_bytes(source),
                "bytes": len(source),
            }
        ]
        scope_tampered_receipt = build_receipt(
            scope_tamper_observation,
            source_locator="fixture://scope-tamper",
            source_digest=digest_bytes(source),
            source_bytes=len(source),
            now=now,
        )
        scope_tampered_receipt["scope"] = "other-business"
        write_receipt(scope_tampered_receipt, full_receipts)
        proc, payload = run(
            "build",
            "--receipts-dir",
            str(full_receipts),
            "--output",
            str(full_manifest),
            "--scope",
            "prepende-operations",
            "--dry-run",
        )
        assert payload["gateCounts"] == {"pass": 10, "fail": 0, "unknown": 0}, payload
        assert payload["diagnostics"]["invalidReceiptCount"] == 3, payload
        assert proc.returncode == 1, payload
        assert payload["ok"] is False, payload

    print("smoke_recovery_receipt_pipeline OK")


if __name__ == "__main__":
    main()
