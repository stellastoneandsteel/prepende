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
            "--dry-run",
        )
        assert proc.returncode == 1, payload
        assert payload["gateCounts"] == {"pass": 0, "fail": 1, "unknown": 9}, payload
        assert payload["diagnostics"]["selected"][0]["status"] == "fail", payload

        invalid = json.loads(json.dumps(template))
        invalid["proofClass"] = "responsive_cli"
        invalid_path = temp / "invalid.json"
        invalid_path.write_text(json.dumps(invalid), encoding="utf-8")
        proc, payload = run("record", "--input", str(invalid_path), "--receipts-dir", str(receipts))
        assert proc.returncode == 2, payload
        assert "requires proofClass" in payload["error"], payload

    print("smoke_recovery_receipt_pipeline OK")


if __name__ == "__main__":
    main()
