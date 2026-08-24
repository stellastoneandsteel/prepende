#!/usr/bin/env python3
"""Evaluate Prepende's cached ten-gate recovery manifest.

This verifier is deliberately provider-free and read-only.  Backup, restore,
Netlify, and Supabase jobs produce evidence receipts separately; this command
only decides whether a fresh manifest proves every required recovery gate.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from operations.continuity import (  # noqa: E402
    RECOVERY_GATE_IDS,
    RECOVERY_SCHEMA_VERSION,
    evaluate_recovery_manifest,
    recovery_manifest_path,
    resolve_recovery_manifest_path,
)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def template() -> dict:
    now = datetime.now(timezone.utc)
    return {
        "schemaVersion": RECOVERY_SCHEMA_VERSION,
        "generatedAt": _iso(now),
        "expiresAt": _iso(now + timedelta(days=31)),
        "receiptSet": {"validCount": 0, "expiredCount": 0, "invalidCount": 0},
        "gates": [
            {
                "id": gate_id,
                "status": "unknown",
                "evidence": [],
            }
            for gate_id in RECOVERY_GATE_IDS
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", help="Recovery manifest path; defaults to PREPENDE_RECOVERY_MANIFEST or the repo-local cache")
    parser.add_argument("--scope", help="Require the manifest to belong to this exact tenant/workspace scope")
    parser.add_argument("--json", action="store_true", help="Print the complete machine-readable result")
    parser.add_argument("--print-template", action="store_true", help="Print an unproven manifest template and exit")
    args = parser.parse_args()

    if args.print_template:
        print(json.dumps(template(), indent=2))
        return 0

    path = (
        Path(args.manifest).expanduser()
        if args.manifest
        else (
            resolve_recovery_manifest_path(ROOT, args.scope)
            if args.scope
            else recovery_manifest_path(ROOT)
        )
    )
    if not path.is_absolute():
        path = ROOT / path
    manifest = None
    error = None
    if path.is_file():
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            error = f"{type(exc).__name__}: {exc}"
    result = evaluate_recovery_manifest(
        manifest,
        manifest_dir=path.parent,
        expected_scope=args.scope,
    )
    if error:
        result["status"] = "unreadable"
        result["reasons"] = [f"recovery_manifest_unreadable:{error}"]
    payload = {
        "ok": bool(result.get("proven")),
        "command": "verify-prepende-recovery",
        "manifest": str(path),
        "result": result,
        "externalActions": [],
        "actionExecuted": False,
        "durableMemoryWrite": False,
    }
    if args.json:
        print(json.dumps(payload, indent=2, default=str))
    elif payload["ok"]:
        print("PREPENDE RECOVERY: PROVEN")
    else:
        print("PREPENDE RECOVERY: UNPROVEN")
        for reason in result.get("reasons", []):
            print(f"- {reason}")
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
