#!/usr/bin/env python3
"""Smoke: continuity V2 separates transport, planning, and recovery truth."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from operations.continuity import (
    DEFAULT_RECOVERY_MANIFEST,
    RECOVERY_GATE_IDS,
    build_continuity_packet,
    evaluate_recovery_manifest,
    load_recovery_evaluation,
    recovery_manifest_path,
    resolve_recovery_manifest_path,
)
from operations.recovery_receipts import (
    GATE_POLICIES,
    build_manifest,
    build_receipt,
    digest_bytes,
    observation_template,
    receipt_digest,
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


def fixture_receipt(
    gate_id: str,
    now: datetime,
    *,
    status: str = "pass",
    expires_at: datetime | None = None,
    scope: str = "prepende-operations",
) -> dict:
    source = b"fixture evidence"
    observation = observation_template(gate_id, now=now)
    observation["scope"] = scope
    if expires_at is not None:
        observation["expiresAt"] = iso(expires_at)
    observation["producer"] = {
        "id": f"fixture-{gate_id}",
        "version": "1",
        "kind": GATE_POLICIES[gate_id]["producerKinds"][0],
    }
    observation["summary"] = f"Fixture proof for {gate_id}."
    observation["checks"] = [
        {"id": check_id, "status": status, "detail": "fixture"}
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
    return build_receipt(
        observation,
        source_locator=f"fixture://{gate_id}",
        source_digest=digest_bytes(source),
        source_bytes=len(source),
        now=now,
    )


def healthy_manifest(
    now: datetime,
    manifest_path: Path,
    *,
    scope: str = "prepende-operations",
) -> dict:
    receipts_dir = manifest_path.parent / "receipts"
    for gate_id in RECOVERY_GATE_IDS:
        write_receipt(fixture_receipt(gate_id, now, scope=scope), receipts_dir)
    manifest, diagnostics = build_manifest(
        receipts_dir=receipts_dir,
        output_path=manifest_path,
        scope=scope,
        now=now,
    )
    assert diagnostics["invalidReceiptCount"] == 0, diagnostics
    return manifest


def main() -> None:
    now = datetime(2026, 7, 16, 18, 0, tzinfo=timezone.utc)
    with tempfile.TemporaryDirectory(prefix="prepende-continuity-v2-") as temp_dir:
        manifest_path = Path(temp_dir) / "recovery.json"
        manifest = healthy_manifest(now, manifest_path)
        result = evaluate_recovery_manifest(
            manifest,
            now=now,
            manifest_dir=manifest_path.parent,
            expected_scope="prepende-operations",
        )
        assert result["proven"] is True, result
        assert result["gateCounts"] == {"pass": 10, "fail": 0, "unknown": 0}, result
        unscoped_result = evaluate_recovery_manifest(
            manifest,
            now=now,
            manifest_dir=manifest_path.parent,
        )
        assert unscoped_result["proven"] is False, unscoped_result
        assert "recovery_expected_scope_missing" in unscoped_result["reasons"], unscoped_result

        authority_path = manifest_path.parent / "authority" / "recovery.json"
        authority_receipts = authority_path.parent / "receipts"
        write_receipt(fixture_receipt("inventory", now - timedelta(days=2)), authority_receipts)
        newer_expired_failure = fixture_receipt(
            "inventory",
            now - timedelta(days=1),
            status="fail",
            expires_at=now - timedelta(seconds=1),
        )
        write_receipt(newer_expired_failure, authority_receipts)
        authority_manifest, authority_diagnostics = build_manifest(
            receipts_dir=authority_receipts,
            output_path=authority_path,
            scope="prepende-operations",
            now=now,
        )
        authority_inventory = next(
            gate for gate in authority_manifest["gates"] if gate["id"] == "inventory"
        )
        assert authority_inventory == {
            "id": "inventory",
            "status": "unknown",
            "evidence": [],
        }, authority_inventory
        assert authority_diagnostics["selected"][0]["status"] == "expired", authority_diagnostics
        assert authority_diagnostics["selected"][0]["receiptStatus"] == "fail", authority_diagnostics

        write_receipt(fixture_receipt("inventory", now), authority_receipts)
        write_receipt(
            fixture_receipt(
                "inventory",
                now,
                expires_at=now + timedelta(hours=1),
            ),
            authority_receipts,
        )
        authority_manifest, authority_diagnostics = build_manifest(
            receipts_dir=authority_receipts,
            output_path=authority_path,
            scope="prepende-operations",
            now=now,
        )
        authority_inventory = next(
            gate for gate in authority_manifest["gates"] if gate["id"] == "inventory"
        )
        assert authority_inventory["status"] == "pass", authority_inventory
        assert authority_diagnostics["selected"][0]["status"] == "pass", authority_diagnostics
        assert authority_manifest["expiresAt"] == iso(now + timedelta(hours=1)), authority_manifest

        write_receipt(
            fixture_receipt("inventory", now, status="fail"),
            authority_receipts,
        )
        authority_manifest, authority_diagnostics = build_manifest(
            receipts_dir=authority_receipts,
            output_path=authority_path,
            scope="prepende-operations",
            now=now,
        )
        authority_inventory = next(
            gate for gate in authority_manifest["gates"] if gate["id"] == "inventory"
        )
        assert authority_inventory["status"] == "unknown", authority_inventory
        assert authority_diagnostics["selected"][0]["status"] == "conflict", authority_diagnostics

        invalid_interval_path = manifest_path.parent / "invalid-interval" / "recovery.json"
        invalid_interval_receipts = invalid_interval_path.parent / "receipts"
        invalid_interval = fixture_receipt("inventory", now - timedelta(days=40))
        invalid_interval["expiresAt"] = iso(now - timedelta(days=41))
        invalid_interval["receiptDigest"] = receipt_digest(invalid_interval)
        write_receipt(invalid_interval, invalid_interval_receipts)
        _, invalid_interval_diagnostics = build_manifest(
            receipts_dir=invalid_interval_receipts,
            output_path=invalid_interval_path,
            scope="prepende-operations",
            now=now,
        )
        assert invalid_interval_diagnostics["expiredReceiptCount"] == 0, invalid_interval_diagnostics
        assert invalid_interval_diagnostics["invalidReceiptCount"] == 1, invalid_interval_diagnostics
        assert "receipt_validity_interval_invalid" in (
            invalid_interval_diagnostics["invalidReceipts"][0]["reasons"]
        ), invalid_interval_diagnostics

        receipts_dir = manifest_path.parent / "receipts"
        expired_receipt = fixture_receipt("inventory", now - timedelta(days=40))
        expired_path = write_receipt(expired_receipt, receipts_dir)
        manifest, diagnostics = build_manifest(
            receipts_dir=receipts_dir,
            output_path=manifest_path,
            scope="prepende-operations",
            now=now,
        )
        assert diagnostics["expiredReceiptCount"] == 1, diagnostics
        assert diagnostics["invalidReceiptCount"] == 0, diagnostics
        assert manifest["receiptSet"]["expiredCount"] == 1, manifest
        result = evaluate_recovery_manifest(
            manifest,
            now=now,
            manifest_dir=manifest_path.parent,
            expected_scope="prepende-operations",
        )
        assert result["proven"] is True, result

        tampered_receipt = json.loads(expired_path.read_text(encoding="utf-8"))
        tampered_receipt["summary"] = "Tampered expired fixture."
        expired_path.write_text(json.dumps(tampered_receipt), encoding="utf-8")
        manifest, diagnostics = build_manifest(
            receipts_dir=receipts_dir,
            output_path=manifest_path,
            scope="prepende-operations",
            now=now,
        )
        assert diagnostics["expiredReceiptCount"] == 0, diagnostics
        assert diagnostics["invalidReceiptCount"] == 1, diagnostics
        result = evaluate_recovery_manifest(
            manifest,
            now=now,
            manifest_dir=manifest_path.parent,
            expected_scope="prepende-operations",
        )
        assert result["proven"] is False, result
        assert "recovery_receipt_set_contains_invalid_receipts" in result["reasons"], result
        expired_path.write_text(json.dumps(expired_receipt), encoding="utf-8")
        manifest, diagnostics = build_manifest(
            receipts_dir=receipts_dir,
            output_path=manifest_path,
            scope="prepende-operations",
            now=now,
        )
        assert diagnostics["invalidReceiptCount"] == 0, diagnostics

        expired = dict(manifest)
        expired["expiresAt"] = iso(now - timedelta(seconds=1))
        result = evaluate_recovery_manifest(
            expired,
            now=now,
            manifest_dir=manifest_path.parent,
            expected_scope="prepende-operations",
        )
        assert result["proven"] is False, result
        assert "recovery_manifest_expired" in result["reasons"], result

        invalid_evidence = json.loads(json.dumps(manifest))
        invalid_evidence["gates"][0]["evidence"][0].pop("digest")
        result = evaluate_recovery_manifest(
            invalid_evidence,
            now=now,
            manifest_dir=manifest_path.parent,
            expected_scope="prepende-operations",
        )
        assert result["proven"] is False, result
        assert "gate_evidence_invalid:inventory" in result["reasons"], result

        missing_invalid_count = json.loads(json.dumps(manifest))
        missing_invalid_count["receiptSet"].pop("invalidCount")
        result = evaluate_recovery_manifest(
            missing_invalid_count,
            now=now,
            manifest_dir=manifest_path.parent,
            expected_scope="prepende-operations",
        )
        assert result["proven"] is False, result
        assert "recovery_receipt_set_invalid" in result["reasons"], result

        previous_default = os.environ.pop("PREPENDE_RECOVERY_MANIFEST", None)
        try:
            scoped_root = manifest_path.parent / "scoped-root"
            scope_a_path = recovery_manifest_path(scoped_root, "scope-a")
            scope_b_path = recovery_manifest_path(scoped_root, "scope-b")
            hostile_scope_path = recovery_manifest_path(scoped_root, "../../Snowman-☃/私")
            assert scope_a_path != scope_b_path
            assert scope_a_path.parent == scoped_root / DEFAULT_RECOVERY_MANIFEST.parent
            assert hostile_scope_path.parent == scope_a_path.parent
            assert "Snowman" not in hostile_scope_path.name
            assert scope_a_path.name == (
                "recovery-manifest-"
                + hashlib.sha256(b"scope-a").hexdigest()
                + ".json"
            )

            healthy_manifest(now, scope_a_path, scope="scope-a")
            healthy_manifest(now, scope_b_path, scope="scope-b")
            scope_a_result, scope_a_source = load_recovery_evaluation(
                scoped_root,
                scope="scope-a",
                now=now,
            )
            scope_b_result, scope_b_source = load_recovery_evaluation(
                scoped_root,
                scope="scope-b",
                now=now,
            )
            assert scope_a_result["proven"] is True, scope_a_result
            assert scope_b_result["proven"] is True, scope_b_result
            assert scope_a_source["path"] != scope_b_source["path"]
            try:
                build_manifest(
                    receipts_dir=scope_a_path.parent / "receipts",
                    output_path=scope_a_path,
                    scope="scope-b",
                    now=now,
                )
            except ValueError as exc:
                assert "different scope" in str(exc), exc
            else:
                raise AssertionError("cross-scope manifest overwrite was accepted")

            legacy_root = manifest_path.parent / "legacy-root"
            legacy_path = legacy_root / DEFAULT_RECOVERY_MANIFEST
            healthy_manifest(now, legacy_path)
            assert resolve_recovery_manifest_path(
                legacy_root,
                "prepende-operations",
            ) == legacy_path
            legacy_result, legacy_source = load_recovery_evaluation(
                legacy_root,
                scope="prepende-operations",
                now=now,
            )
            assert legacy_result["proven"] is True, legacy_result
            assert legacy_source["path"] == str(legacy_path)
            wrong_legacy_result, wrong_legacy_source = load_recovery_evaluation(
                legacy_root,
                scope="other-scope",
                now=now,
            )
            assert wrong_legacy_result["proven"] is False, wrong_legacy_result
            assert "recovery_manifest_scope_mismatch" in wrong_legacy_result["reasons"]
            assert wrong_legacy_source["path"] == str(legacy_path)

            broken_scoped_path = recovery_manifest_path(
                legacy_root,
                "prepende-operations",
            )
            broken_scoped_path.write_text("{broken", encoding="utf-8")
            assert resolve_recovery_manifest_path(
                legacy_root,
                "prepende-operations",
            ) == broken_scoped_path
            broken_result, broken_source = load_recovery_evaluation(
                legacy_root,
                scope="prepende-operations",
                now=now,
            )
            assert broken_result["proven"] is False, broken_result
            assert broken_result["reasons"][0].startswith("recovery_manifest_unreadable"), broken_result
            assert broken_source["path"] == str(broken_scoped_path)

            wrong_type_root = manifest_path.parent / "wrong-type-root"
            wrong_type_legacy = wrong_type_root / DEFAULT_RECOVERY_MANIFEST
            healthy_manifest(now, wrong_type_legacy)
            wrong_type_scoped = recovery_manifest_path(
                wrong_type_root,
                "prepende-operations",
            )
            wrong_type_scoped.mkdir()
            wrong_type_result, wrong_type_source = load_recovery_evaluation(
                wrong_type_root,
                scope="prepende-operations",
                now=now,
            )
            assert wrong_type_result["proven"] is False, wrong_type_result
            assert wrong_type_source["path"] == str(wrong_type_scoped)

            explicit_path = manifest_path.parent / "explicit-missing.json"
            os.environ["PREPENDE_RECOVERY_MANIFEST"] = str(explicit_path)
            assert resolve_recovery_manifest_path(
                legacy_root,
                "prepende-operations",
            ) == explicit_path
            explicit_result, explicit_source = load_recovery_evaluation(
                legacy_root,
                scope="prepende-operations",
                now=now,
            )
            assert explicit_result["proven"] is False, explicit_result
            assert explicit_source["path"] == str(explicit_path)

            os.environ["PREPENDE_RECOVERY_MANIFEST"] = str(legacy_path)
            wrong_explicit_result, wrong_explicit_source = load_recovery_evaluation(
                legacy_root,
                scope="other-scope",
                now=now,
            )
            assert wrong_explicit_result["proven"] is False, wrong_explicit_result
            assert "recovery_manifest_scope_mismatch" in wrong_explicit_result["reasons"]
            assert wrong_explicit_source["path"] == str(legacy_path)
        finally:
            if previous_default is None:
                os.environ.pop("PREPENDE_RECOVERY_MANIFEST", None)
            else:
                os.environ["PREPENDE_RECOVERY_MANIFEST"] = previous_default

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
