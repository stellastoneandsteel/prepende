#!/usr/bin/env python3
"""Create and assemble fail-closed Prepende recovery receipts.

This command records evidence produced by a collector or controlled drill.  It
does not run Netlify, Supabase, credential, or lost-machine recovery itself.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from operations.continuity import RECOVERY_GATE_IDS, recovery_manifest_path  # noqa: E402
from operations.recovery_receipts import (  # noqa: E402
    DEFAULT_RECEIPTS_DIR,
    GATE_POLICIES,
    build_manifest,
    build_receipt,
    canonical_json,
    collect_restore_drill,
    digest_bytes,
    observation_template,
    receipt_from_observation_path,
    write_receipt,
)


def _path(raw: str | None, default: Path) -> Path:
    path = Path(raw).expanduser() if raw else default
    return path if path.is_absolute() else ROOT / path


def _receipts_dir(raw: str | None) -> Path:
    return _path(raw, ROOT / DEFAULT_RECEIPTS_DIR)


def _print(payload: dict, *, compact: bool = False) -> None:
    print(json.dumps(payload, separators=(",", ":") if compact else None, indent=None if compact else 2, default=str))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    template_parser = subparsers.add_parser("template", help="Print an unproven producer observation template")
    template_parser.add_argument("--gate", choices=RECOVERY_GATE_IDS, required=True)

    record_parser = subparsers.add_parser("record", help="Validate a producer observation and write an immutable receipt")
    record_parser.add_argument("--input", required=True, help="Observation JSON produced by a collector or controlled drill")
    record_parser.add_argument("--receipts-dir")

    collect_parser = subparsers.add_parser("collect-restore-drill", help="Import the latest existing Prepende restore-drill result")
    collect_parser.add_argument("--log", default=".engram/restore-drills.jsonl")
    collect_parser.add_argument("--receipts-dir")
    collect_parser.add_argument("--scope", default="prepende-operations")

    build_parser = subparsers.add_parser("build", help="Build the cached ten-gate manifest from the newest valid receipts")
    build_parser.add_argument("--receipts-dir")
    build_parser.add_argument("--output", help="Manifest path; defaults to PREPENDE_RECOVERY_MANIFEST or the repo cache")
    build_parser.add_argument("--scope", default="prepende-operations")
    build_parser.add_argument("--dry-run", action="store_true", help="Evaluate and print without writing the manifest")

    gap_parser = subparsers.add_parser(
        "record-gap",
        help="Write a fresh fail-closed receipt for a gate not proven by the controlled rehearsal",
    )
    gap_parser.add_argument("--gate", choices=RECOVERY_GATE_IDS, required=True)
    gap_parser.add_argument("--scope", required=True)
    gap_parser.add_argument("--summary", required=True)
    gap_parser.add_argument("--receipts-dir")

    args = parser.parse_args()
    try:
        if args.command == "template":
            _print(observation_template(args.gate))
            return 0

        if args.command == "record":
            receipt, path = receipt_from_observation_path(
                _path(args.input, ROOT / args.input),
                receipts_dir=_receipts_dir(args.receipts_dir),
            )
            _print(
                {
                    "ok": True,
                    "command": "record",
                    "receiptId": receipt["receiptId"],
                    "gateId": receipt["gateId"],
                    "status": receipt["status"],
                    "receiptPath": str(path),
                    "durableMemoryWrite": False,
                    "externalActions": [],
                }
            )
            return 0

        if args.command == "collect-restore-drill":
            receipt, path = collect_restore_drill(
                log_path=_path(args.log, ROOT / ".engram/restore-drills.jsonl"),
                receipts_dir=_receipts_dir(args.receipts_dir),
                scope=args.scope,
            )
            _print(
                {
                    "ok": receipt["status"] == "pass",
                    "command": "collect-restore-drill",
                    "receiptId": receipt["receiptId"],
                    "gateId": receipt["gateId"],
                    "status": receipt["status"],
                    "receiptPath": str(path),
                    "durableMemoryWrite": False,
                    "externalActions": [],
                }
            )
            return 0 if receipt["status"] == "pass" else 1

        if args.command == "record-gap":
            observation = observation_template(args.gate)
            observation["scope"] = args.scope.strip()
            observation["producer"] = {
                "id": "controlled-healing-rehearsal-gap",
                "version": "1",
                "kind": GATE_POLICIES[args.gate]["producerKinds"][0],
            }
            observation["summary"] = args.summary.strip()[:1000]
            observation["checks"] = [
                {
                    "id": check["id"],
                    "status": "fail",
                    "detail": "Required proof was not produced by this bounded local rehearsal.",
                }
                for check in observation["checks"]
            ]
            observation["safety"] = {
                "isolation": "local_fail_closed_rehearsal",
                "productionMutated": False,
                "secretsStored": False,
                "externalActions": [],
            }
            source = canonical_json(observation).encode("utf-8")
            receipt = build_receipt(
                observation,
                source_locator=f"generated://controlled-rehearsal-gap/{args.gate}",
                source_digest=digest_bytes(source),
                source_bytes=len(source),
            )
            path = write_receipt(receipt, _receipts_dir(args.receipts_dir))
            _print(
                {
                    "ok": True,
                    "command": "record-gap",
                    "receiptId": receipt["receiptId"],
                    "gateId": receipt["gateId"],
                    "status": receipt["status"],
                    "receiptPath": str(path),
                    "durableMemoryWrite": False,
                    "externalActions": [],
                }
            )
            return 0

        scope = args.scope
        if not scope.strip() or scope != scope.strip():
            raise ValueError("scope must be a non-empty canonical string")
        output = (
            _path(args.output, recovery_manifest_path(ROOT, scope))
            if args.output
            else recovery_manifest_path(ROOT, scope)
        )
        manifest, diagnostics = build_manifest(
            receipts_dir=_receipts_dir(args.receipts_dir),
            output_path=output,
            scope=scope,
            write=not args.dry_run,
        )
        counts = {"pass": 0, "fail": 0, "unknown": 0}
        for gate in manifest["gates"]:
            counts[gate["status"]] += 1
        proven = (
            counts["pass"] == len(RECOVERY_GATE_IDS)
            and diagnostics["invalidReceiptCount"] == 0
        )
        _print(
            {
                "ok": proven,
                "command": "build",
                "wroteManifest": not args.dry_run,
                "manifestPath": str(output),
                "gateCounts": counts,
                "diagnostics": diagnostics,
                "durableMemoryWrite": False,
                "externalActions": [],
            }
        )
        return 0 if proven else 1
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        _print(
            {
                "ok": False,
                "command": args.command,
                "error": f"{type(exc).__name__}: {exc}",
                "durableMemoryWrite": False,
                "externalActions": [],
            }
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
