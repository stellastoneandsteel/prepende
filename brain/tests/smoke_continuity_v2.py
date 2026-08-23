#!/usr/bin/env python3
"""Smoke: continuity V2 separates transport, planning, and recovery truth."""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from operations.continuity import (
    RECOVERY_GATE_IDS,
    build_continuity_packet,
    evaluate_recovery_manifest,
)
from operations.recovery_receipts import (
    GATE_POLICIES,
    build_manifest,
    build_receipt,
    digest_bytes,
    observation_template,
    write_receipt,
)


def iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def rag_status(**updates: object) -> dict:
    status = {
        "source_files": 0,
        "indexed_files": 0,
        "chunks": 0,
        "embedded_chunks": 0,
        "missing_embeddings": 0,
        "lexical_ready": False,
        "stale": False,
    }
    status.update(updates)
    return status


def healthy_manifest(now: datetime, manifest_path: Path) -> dict:
    receipts_dir = manifest_path.parent / "receipts"
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
    manifest, diagnostics = build_manifest(
        receipts_dir=receipts_dir,
        output_path=manifest_path,
        scope="prepende-operations",
        now=now,
    )
    assert diagnostics["invalidReceiptCount"] == 0, diagnostics
    return manifest


def main() -> None:
    now = datetime(2026, 7, 16, 18, 0, tzinfo=timezone.utc)
    with tempfile.TemporaryDirectory(prefix="prepende-continuity-v2-") as temp_dir:
        manifest_path = Path(temp_dir) / "recovery.json"
        manifest = healthy_manifest(now, manifest_path)
        result = evaluate_recovery_manifest(manifest, now=now, manifest_dir=manifest_path.parent)
        assert result["proven"] is True, result
        assert result["gateCounts"] == {"pass": 10, "fail": 0, "unknown": 0}, result

        expired = dict(manifest)
        expired["expiresAt"] = iso(now - timedelta(seconds=1))
        result = evaluate_recovery_manifest(expired, now=now, manifest_dir=manifest_path.parent)
        assert result["proven"] is False, result
        assert "recovery_manifest_expired" in result["reasons"], result

        invalid_evidence = json.loads(json.dumps(manifest))
        invalid_evidence["gates"][0]["evidence"][0].pop("digest")
        result = evaluate_recovery_manifest(invalid_evidence, now=now, manifest_dir=manifest_path.parent)
        assert result["proven"] is False, result
        assert "gate_evidence_invalid:inventory" in result["reasons"], result

        import os

        previous = os.environ.get("PREPENDE_RECOVERY_MANIFEST")
        os.environ["PREPENDE_RECOVERY_MANIFEST"] = str(manifest_path)
        try:
            status = {
                "knowledge": {
                    "rag": rag_status(
                        source_files=1,
                        indexed_files=1,
                        chunks=1,
                        missing_embeddings=1,
                        lexical_ready=True,
                    ),
                    "graphify": {"ready": False, "reason": "source_hash_mismatch"},
                },
                "connectors": {"tools": 3, "ready": 0},
            }
            packet = build_continuity_packet(
                root=ROOT,
                goal="prove continuity semantics",
                scope="prepende-operations",
                profile="general",
                status_payload=status,
                transport_ok=True,
                now=now,
            )
        finally:
            if previous is None:
                os.environ.pop("PREPENDE_RECOVERY_MANIFEST", None)
            else:
                os.environ["PREPENDE_RECOVERY_MANIFEST"] = previous

        assert packet["verdict"]["transportOk"] is True, packet
        assert packet["verdict"]["continuityReady"] is True, packet
        assert packet["verdict"]["recoveryProven"] is True, packet
        assert any(item["id"] == "graph_projection_stale" and item["severity"] == "advisory" for item in packet["blockers"]), packet
        assert packet["packetId"].startswith("sha256:"), packet

        empty_status = {"knowledge": {"rag": rag_status()}}
        coding = build_continuity_packet(
            root=ROOT,
            goal="edit source without tenant knowledge",
            scope="tenant-alpha",
            profile="coding",
            status_payload=empty_status,
            transport_ok=True,
            now=now,
        )
        assert coding["verdict"]["continuityReady"] is True, coding
        assert coding["verdict"]["planReady"] is True, coding
        assert coding["verdict"]["recoveryProven"] is coding["recovery"]["proven"], coding
        assert any(
            item["id"] == "rag_empty_coding_scope"
            and item["severity"] == "advisory"
            and item["blocks"] == []
            for item in coding["blockers"]
        ), coding

        general = build_continuity_packet(
            root=ROOT,
            goal="write from tenant knowledge",
            scope="tenant-alpha",
            profile="general",
            status_payload=empty_status,
            transport_ok=True,
            now=now,
        )
        assert general["verdict"]["continuityReady"] is False, general
        assert any(item["id"] == "rag_lexical_unavailable" for item in general["blockers"]), general

        for label, invalid_rag, blocker_id in (
            ("stale empty", rag_status(stale=True), "rag_status_invalid"),
            ("source without chunks", rag_status(source_files=1, indexed_files=1), "rag_lexical_unavailable"),
            ("chunks without sources", rag_status(chunks=1, missing_embeddings=1, lexical_ready=True), "rag_lexical_unavailable"),
            ("partial index", rag_status(source_files=2, indexed_files=1, chunks=1, missing_embeddings=1), "rag_lexical_unavailable"),
            ("missing status", None, "rag_status_invalid"),
            ("malformed status", {"lexical_ready": False, "stale": False}, "rag_status_invalid"),
        ):
            invalid = build_continuity_packet(
                root=ROOT,
                goal=f"reject {label}",
                scope="tenant-alpha",
                profile="coding",
                status_payload={"knowledge": {"rag": invalid_rag}},
                transport_ok=True,
                now=now,
            )
            assert invalid["verdict"]["continuityReady"] is False, invalid
            assert any(item["id"] == blocker_id for item in invalid["blockers"]), invalid

    failed = build_continuity_packet(
        root=ROOT,
        goal="prove transport failure",
        scope="prepende-operations",
        profile="general",
        status_payload={"ok": False},
        transport_ok=False,
        now=now,
    )
    assert failed["verdict"]["transportOk"] is False, failed
    assert failed["verdict"]["continuityReady"] is False, failed
    assert any(item["id"] == "status_transport_failed" for item in failed["blockers"]), failed

    print("smoke_continuity_v2 OK")


if __name__ == "__main__":
    main()
