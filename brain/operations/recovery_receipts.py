"""Provenance-bound recovery receipts and manifest assembly for Prepende.

Collectors and controlled drills produce observation JSON.  This module turns
those observations into immutable local receipts, validates their exact gate
requirements, and assembles the cached manifest consumed by Fast Continuity.
It never contacts a provider or performs a restore itself.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from operations.continuity import (
    DEFAULT_RECOVERY_EVIDENCE_MAX_AGE_DAYS,
    RECOVERY_GATE_IDS,
    RECOVERY_SCHEMA_VERSION,
)


OBSERVATION_SCHEMA_VERSION = "prepende-recovery-observation-v1"
RECEIPT_SCHEMA_VERSION = "prepende-recovery-receipt-v1"
DEFAULT_RECEIPTS_DIR = Path(".engram/continuity/recovery-receipts")
MAX_EVIDENCE_ARTIFACT_BYTES = 25 * 1024 * 1024

GATE_POLICIES: dict[str, dict[str, Any]] = {
    "inventory": {
        "proofClass": "inventory_snapshot",
        "producerKinds": ("automated_collector", "owner_attestation"),
        "checks": (
            "all_assets_registered",
            "owners_assigned",
            "rpo_rto_defined",
            "dependencies_mapped",
        ),
    },
    "source_recovery": {
        "proofClass": "source_restore_drill",
        "producerKinds": ("controlled_drill",),
        "checks": (
            "off_device_source_available",
            "fresh_clone_succeeded",
            "expected_revision_present",
            "build_or_smoke_passed",
        ),
    },
    "work_in_progress_recovery": {
        "proofClass": "wip_restore_drill",
        "producerKinds": ("controlled_drill",),
        "checks": (
            "uncommitted_snapshot_available",
            "snapshot_off_device",
            "clean_workspace_restore_succeeded",
            "restored_diff_matched",
        ),
    },
    "prepende_recovery": {
        "proofClass": "prepende_restore_drill",
        "producerKinds": ("automated_collector", "controlled_drill"),
        "checks": (
            "backup_available",
            "memory_restored",
            "vault_restored",
            "rag_rebuilt",
        ),
    },
    "assistant_continuity": {
        "proofClass": "assistant_restore_drill",
        "producerKinds": ("controlled_drill",),
        "checks": (
            "assistant_config_export_available",
            "config_restore_succeeded",
            "mcp_registration_restored",
            "prepende_handoff_succeeded",
        ),
    },
    "netlify_recovery": {
        "proofClass": "netlify_restore_drill",
        "producerKinds": ("controlled_external_drill",),
        "checks": (
            "site_config_captured",
            "masked_environment_inventory_captured",
            "isolated_redeploy_succeeded",
            "routes_and_functions_passed",
            "source_revision_matched",
        ),
    },
    "supabase_recovery": {
        "proofClass": "supabase_restore_drill",
        "producerKinds": ("controlled_external_drill",),
        "checks": (
            "schema_backup_available",
            "data_backup_available",
            "isolated_restore_succeeded",
            "rls_and_auth_verified",
            "row_counts_or_checksums_matched",
        ),
    },
    "credential_recovery": {
        "proofClass": "credential_recovery_drill",
        "producerKinds": ("controlled_drill", "owner_attestation"),
        "checks": (
            "credential_inventory_complete",
            "owner_recovery_path_verified",
            "rotation_procedure_tested",
            "no_secret_material_in_receipt",
        ),
    },
    "lost_machine_drill": {
        "proofClass": "lost_machine_drill",
        "producerKinds": ("controlled_drill",),
        "checks": (
            "replacement_or_isolated_host_used",
            "source_restored",
            "prepende_restored",
            "hosted_services_reconnected",
            "acceptance_suite_passed",
        ),
    },
    "failure_detection": {
        "proofClass": "recovery_monitor_canary",
        "producerKinds": ("controlled_drill", "controlled_external_drill"),
        "checks": (
            "backup_failure_canary_detected",
            "restore_failure_canary_detected",
            "alert_delivered",
            "owner_acknowledged",
            "receipt_persisted",
        ),
    },
}

_FORBIDDEN_KEYS = {
    "api_key",
    "apikey",
    "token",
    "access_token",
    "accesstoken",
    "refresh_token",
    "refreshtoken",
    "secret",
    "password",
    "private_key",
    "privatekey",
    "service_role_key",
    "servicerolekey",
    "secret_key",
    "secretkey",
}
_ALLOWED_SAFETY_KEYS = {"secretsStored"}


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)


def digest_value(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def digest_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _secret_key_paths(value: Any, prefix: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            normalized = str(key).strip().lower().replace("-", "_")
            if key not in _ALLOWED_SAFETY_KEYS and normalized in _FORBIDDEN_KEYS:
                found.append(path)
            found.extend(_secret_key_paths(child, path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_secret_key_paths(child, f"{prefix}[{index}]"))
    return found


def receipt_digest(receipt: dict[str, Any]) -> str:
    material = dict(receipt)
    material.pop("receiptDigest", None)
    return digest_value(material)


def observation_template(gate_id: str, *, now: datetime | None = None) -> dict[str, Any]:
    if gate_id not in GATE_POLICIES:
        raise ValueError(f"unsupported recovery gate: {gate_id}")
    observed = now or datetime.now(timezone.utc)
    policy = GATE_POLICIES[gate_id]
    return {
        "schemaVersion": OBSERVATION_SCHEMA_VERSION,
        "gateId": gate_id,
        "scope": "prepende-operations",
        "proofClass": policy["proofClass"],
        "observedAt": _iso(observed),
        "expiresAt": _iso(observed + timedelta(days=DEFAULT_RECOVERY_EVIDENCE_MAX_AGE_DAYS)),
        "producer": {
            "id": "replace-with-collector-or-drill-id",
            "version": "replace-with-version",
            "kind": policy["producerKinds"][0],
        },
        "summary": "Replace with a bounded, non-secret result summary.",
        "checks": [
            {"id": check_id, "status": "unknown", "detail": "Not yet run."}
            for check_id in policy["checks"]
        ],
        "artifacts": [],
        "safety": {
            "isolation": "replace-with-read_only-temporary_local-isolated_provider_project-or-replacement_device",
            "productionMutated": False,
            "secretsStored": False,
            "externalActions": [],
        },
    }


def materialize_artifacts(artifacts: Any, *, base_dir: Path) -> list[dict[str, Any]]:
    if not isinstance(artifacts, list):
        raise ValueError("artifacts must be a list")
    materialized: list[dict[str, Any]] = []
    for index, item in enumerate(artifacts):
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"].strip():
            raise ValueError(f"artifact {index} requires a non-empty id")
        raw_path = item.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ValueError(f"artifact {item['id']} requires a local path")
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = base_dir / path
        path = path.resolve()
        if not path.is_file():
            raise ValueError(f"artifact {item['id']} is not a readable file: {path}")
        if path.stat().st_size > MAX_EVIDENCE_ARTIFACT_BYTES:
            raise ValueError(
                f"artifact {item['id']} exceeds {MAX_EVIDENCE_ARTIFACT_BYTES} bytes; "
                "record a bounded verifier output, not the backup payload"
            )
        body = path.read_bytes()
        materialized.append(
            {
                "id": item["id"].strip(),
                "locator": str(path),
                "digest": digest_bytes(body),
                "bytes": len(body),
            }
        )
    return materialized


def build_receipt(
    observation: Any,
    *,
    source_locator: str,
    source_digest: str,
    source_bytes: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    observed_now = now or datetime.now(timezone.utc)
    if not isinstance(observation, dict):
        raise ValueError("observation must be a JSON object")
    secret_paths = _secret_key_paths(observation)
    if secret_paths:
        raise ValueError("secret-shaped keys are forbidden in recovery evidence: " + ", ".join(secret_paths))
    if observation.get("schemaVersion") != OBSERVATION_SCHEMA_VERSION:
        raise ValueError(f"observation schema must be {OBSERVATION_SCHEMA_VERSION}")
    gate_id = observation.get("gateId")
    if gate_id not in GATE_POLICIES or gate_id not in RECOVERY_GATE_IDS:
        raise ValueError(f"unsupported recovery gate: {gate_id}")
    policy = GATE_POLICIES[gate_id]
    if observation.get("proofClass") != policy["proofClass"]:
        raise ValueError(f"{gate_id} requires proofClass={policy['proofClass']}")

    observed_at = _parse_time(observation.get("observedAt"))
    expires_at = _parse_time(observation.get("expiresAt"))
    if observed_at is None or expires_at is None or expires_at <= observed_at:
        raise ValueError("observation requires valid observedAt and later expiresAt")
    if observed_at > observed_now + timedelta(minutes=5):
        raise ValueError("observation observedAt is in the future")
    if expires_at > observed_at + timedelta(days=DEFAULT_RECOVERY_EVIDENCE_MAX_AGE_DAYS):
        raise ValueError("observation validity exceeds the recovery evidence maximum age")

    producer = observation.get("producer")
    if not isinstance(producer, dict):
        raise ValueError("producer is required")
    if producer.get("kind") not in policy["producerKinds"]:
        raise ValueError(f"{gate_id} requires producer kind in {policy['producerKinds']}")
    for field in ("id", "version"):
        value = producer.get(field)
        if not isinstance(value, str) or not value.strip() or value.startswith("replace-"):
            raise ValueError(f"producer.{field} must identify the real producer")

    checks = observation.get("checks")
    if not isinstance(checks, list):
        raise ValueError("checks must be a list")
    by_id: dict[str, dict[str, Any]] = {}
    for check in checks:
        if not isinstance(check, dict) or not isinstance(check.get("id"), str):
            raise ValueError("every check requires an id")
        if check["id"] in by_id:
            raise ValueError(f"duplicate check: {check['id']}")
        if check.get("status") not in {"pass", "fail"}:
            raise ValueError(f"check {check['id']} must be pass or fail")
        by_id[check["id"]] = check
    missing_checks = [check_id for check_id in policy["checks"] if check_id not in by_id]
    if missing_checks:
        raise ValueError("missing required checks: " + ", ".join(missing_checks))
    status = "pass" if checks and all(check["status"] == "pass" for check in checks) else "fail"

    artifacts = observation.get("artifacts")
    if not isinstance(artifacts, list):
        raise ValueError("materialized artifacts must be a list")
    artifact_ids: set[str] = set()
    for artifact in artifacts:
        if (
            not isinstance(artifact, dict)
            or not isinstance(artifact.get("id"), str)
            or not isinstance(artifact.get("locator"), str)
            or not isinstance(artifact.get("digest"), str)
            or not artifact["digest"].startswith("sha256:")
            or len(artifact["digest"]) != 71
        ):
            raise ValueError("every artifact requires id, locator, and SHA-256 digest")
        if artifact["id"] in artifact_ids:
            raise ValueError(f"duplicate artifact: {artifact['id']}")
        artifact_ids.add(artifact["id"])
    if status == "pass" and not artifacts:
        raise ValueError("a passing receipt requires at least one preserved evidence artifact")

    safety = observation.get("safety")
    if not isinstance(safety, dict):
        raise ValueError("safety metadata is required")
    if safety.get("secretsStored") is not False:
        raise ValueError("recovery receipts must explicitly report secretsStored=false")
    if safety.get("productionMutated") is not False:
        raise ValueError("recovery proof must use read-only or isolated targets, not mutate production")
    if not isinstance(safety.get("externalActions"), list):
        raise ValueError("safety.externalActions must be a list")

    summary = observation.get("summary")
    if not isinstance(summary, str) or not summary.strip() or len(summary) > 1000:
        raise ValueError("summary must be a non-empty string of at most 1000 characters")
    scope = observation.get("scope")
    if not isinstance(scope, str) or not scope.strip():
        raise ValueError("scope is required")

    material: dict[str, Any] = {
        "schemaVersion": RECEIPT_SCHEMA_VERSION,
        "gateId": gate_id,
        "scope": scope.strip(),
        "proofClass": policy["proofClass"],
        "status": status,
        "observedAt": _iso(observed_at),
        "expiresAt": _iso(expires_at),
        "producer": {
            "id": producer["id"].strip(),
            "version": producer["version"].strip(),
            "kind": producer["kind"],
        },
        "summary": summary.strip(),
        "checks": checks,
        "artifacts": artifacts,
        "safety": safety,
        "sourceObservation": {
            "locator": source_locator,
            "digest": source_digest,
            "bytes": source_bytes,
        },
        "durableMemoryWrite": False,
    }
    content_hash = digest_value(material)
    material["receiptId"] = f"rr_{gate_id}_{content_hash.removeprefix('sha256:')[:16]}"
    material["receiptDigest"] = receipt_digest(material)
    return material


def validate_receipt(
    receipt: Any,
    *,
    now: datetime | None = None,
    require_fresh: bool = True,
) -> tuple[bool, list[str]]:
    observed_now = now or datetime.now(timezone.utc)
    reasons: list[str] = []
    if not isinstance(receipt, dict):
        return False, ["receipt_not_object"]
    if receipt.get("schemaVersion") != RECEIPT_SCHEMA_VERSION:
        reasons.append("receipt_schema_mismatch")
    gate_id = receipt.get("gateId")
    policy = GATE_POLICIES.get(gate_id)
    if policy is None:
        reasons.append("receipt_gate_invalid")
    if receipt.get("receiptDigest") != receipt_digest(receipt):
        reasons.append("receipt_digest_mismatch")
    if _secret_key_paths(receipt):
        reasons.append("receipt_secret_fields_present")
    receipt_id = receipt.get("receiptId")
    if not isinstance(receipt_id, str) or not receipt_id.startswith(f"rr_{gate_id}_"):
        reasons.append("receipt_id_invalid")
    observed_at = _parse_time(receipt.get("observedAt"))
    expires_at = _parse_time(receipt.get("expiresAt"))
    if observed_at is None:
        reasons.append("receipt_observed_at_invalid")
    elif observed_at > observed_now + timedelta(minutes=5):
        reasons.append("receipt_observed_in_future")
    elif require_fresh and observed_at < observed_now - timedelta(days=DEFAULT_RECOVERY_EVIDENCE_MAX_AGE_DAYS):
        reasons.append("receipt_too_old")
    if expires_at is None:
        reasons.append("receipt_expires_at_invalid")
    elif require_fresh and expires_at <= observed_now:
        reasons.append("receipt_expired")
    elif observed_at is not None and expires_at > observed_at + timedelta(days=DEFAULT_RECOVERY_EVIDENCE_MAX_AGE_DAYS):
        reasons.append("receipt_validity_too_long")

    if policy is not None:
        if receipt.get("proofClass") != policy["proofClass"]:
            reasons.append("receipt_proof_class_invalid")
        producer = receipt.get("producer")
        if not isinstance(producer, dict) or producer.get("kind") not in policy["producerKinds"]:
            reasons.append("receipt_producer_invalid")
        elif any(not isinstance(producer.get(field), str) or not producer[field].strip() for field in ("id", "version")):
            reasons.append("receipt_producer_invalid")
        checks = receipt.get("checks")
        by_id = {
            check.get("id"): check
            for check in checks
            if isinstance(checks, list) and isinstance(check, dict) and isinstance(check.get("id"), str)
        } if isinstance(checks, list) else {}
        if any(check_id not in by_id for check_id in policy["checks"]):
            reasons.append("receipt_required_checks_missing")
        else:
            derived_status = "pass" if checks and all(check.get("status") == "pass" for check in checks) else "fail"
            if receipt.get("status") != derived_status:
                reasons.append("receipt_status_mismatch")
    artifacts = receipt.get("artifacts")
    if receipt.get("status") == "pass" and (not isinstance(artifacts, list) or not artifacts):
        reasons.append("receipt_artifacts_missing")
    if isinstance(artifacts, list):
        for artifact in artifacts:
            if (
                not isinstance(artifact, dict)
                or not isinstance(artifact.get("id"), str)
                or not artifact["id"].strip()
                or not isinstance(artifact.get("locator"), str)
                or not artifact["locator"].strip()
                or not isinstance(artifact.get("digest"), str)
                or not artifact["digest"].startswith("sha256:")
                or len(artifact["digest"]) != 71
            ):
                reasons.append("receipt_artifact_invalid")
                break
    safety = receipt.get("safety")
    if (
        not isinstance(safety, dict)
        or safety.get("secretsStored") is not False
        or safety.get("productionMutated") is not False
        or not isinstance(safety.get("isolation"), str)
        or not safety["isolation"].strip()
        or not isinstance(safety.get("externalActions"), list)
    ):
        reasons.append("receipt_safety_invalid")
    source_observation = receipt.get("sourceObservation")
    if (
        not isinstance(source_observation, dict)
        or not isinstance(source_observation.get("locator"), str)
        or not source_observation["locator"].strip()
        or not isinstance(source_observation.get("digest"), str)
        or not source_observation["digest"].startswith("sha256:")
        or len(source_observation["digest"]) != 71
    ):
        reasons.append("receipt_source_observation_invalid")
    if receipt.get("durableMemoryWrite") is not False:
        reasons.append("receipt_memory_policy_invalid")
    return not reasons, reasons


def write_receipt(receipt: dict[str, Any], receipts_dir: Path) -> Path:
    receipts_dir.mkdir(parents=True, exist_ok=True)
    path = receipts_dir / f"{receipt['receiptId']}.json"
    body = json.dumps(receipt, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    if path.exists():
        if path.read_text(encoding="utf-8") != body:
            raise ValueError(f"refusing to overwrite non-identical receipt: {path}")
        return path
    path.write_text(body, encoding="utf-8")
    return path


def receipt_from_observation_path(
    path: Path,
    *,
    receipts_dir: Path,
    now: datetime | None = None,
) -> tuple[dict[str, Any], Path]:
    source = path.expanduser().resolve()
    body = source.read_bytes()
    observation = json.loads(body.decode("utf-8"))
    if isinstance(observation, dict):
        observation = dict(observation)
        observation["artifacts"] = materialize_artifacts(observation.get("artifacts", []), base_dir=source.parent)
    receipt = build_receipt(
        observation,
        source_locator=str(source),
        source_digest=digest_bytes(body),
        source_bytes=len(body),
        now=now,
    )
    return receipt, write_receipt(receipt, receipts_dir)


def _receipt_reference(receipt: dict[str, Any], receipt_path: Path, manifest_dir: Path) -> dict[str, Any]:
    resolved_dir = manifest_dir.resolve()
    resolved_receipt = receipt_path.resolve()
    try:
        relative_path = resolved_receipt.relative_to(resolved_dir)
    except ValueError as exc:
        raise ValueError("recovery receipts must live beneath the manifest directory") from exc
    return {
        "receiptId": receipt["receiptId"],
        "gateId": receipt["gateId"],
        "observedAt": receipt["observedAt"],
        "digest": receipt["receiptDigest"],
        "receiptPath": relative_path.as_posix(),
        "proofClass": receipt["proofClass"],
        "producerId": receipt["producer"]["id"],
    }


def validate_receipt_reference(
    reference: Any,
    *,
    gate_id: str,
    manifest_dir: Path | None,
    now: datetime | None = None,
) -> tuple[bool, list[str]]:
    if not isinstance(reference, dict):
        return False, ["receipt_reference_not_object"]
    required = ("receiptId", "gateId", "observedAt", "digest", "receiptPath", "proofClass", "producerId")
    if any(not isinstance(reference.get(field), str) or not reference[field] for field in required):
        return False, ["receipt_reference_fields_missing"]
    if reference.get("gateId") != gate_id:
        return False, ["receipt_reference_gate_mismatch"]
    if manifest_dir is None:
        return False, ["receipt_reference_base_missing"]
    raw_path = Path(reference["receiptPath"])
    if raw_path.is_absolute() or ".." in raw_path.parts:
        return False, ["receipt_reference_path_unsafe"]
    base = manifest_dir.resolve()
    path = (base / raw_path).resolve()
    try:
        path.relative_to(base)
    except ValueError:
        return False, ["receipt_reference_path_unsafe"]
    if not path.is_file():
        return False, ["receipt_reference_missing"]
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False, ["receipt_reference_unreadable"]
    valid, reasons = validate_receipt(receipt, now=now)
    if not valid:
        return False, reasons
    comparisons = {
        "receiptId": receipt.get("receiptId"),
        "gateId": receipt.get("gateId"),
        "observedAt": receipt.get("observedAt"),
        "digest": receipt.get("receiptDigest"),
        "proofClass": receipt.get("proofClass"),
        "producerId": receipt.get("producer", {}).get("id") if isinstance(receipt.get("producer"), dict) else None,
    }
    mismatches = [field for field, value in comparisons.items() if reference.get(field) != value]
    if mismatches:
        return False, ["receipt_reference_mismatch:" + ",".join(mismatches)]
    return True, []


def build_manifest(
    *,
    receipts_dir: Path,
    output_path: Path,
    scope: str = "prepende-operations",
    now: datetime | None = None,
    write: bool = True,
) -> tuple[dict[str, Any], dict[str, Any]]:
    observed_now = now or datetime.now(timezone.utc)
    output = output_path.expanduser().resolve()
    receipt_root = receipts_dir.expanduser().resolve()
    valid_receipts: list[tuple[datetime, dict[str, Any], Path]] = []
    invalid_receipts: list[dict[str, Any]] = []
    other_scope_receipts: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    if receipt_root.is_dir():
        for path in sorted(receipt_root.glob("rr_*.json")):
            try:
                receipt = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                invalid_receipts.append({"path": str(path), "reasons": [f"unreadable:{type(exc).__name__}"]})
                continue
            # Scope is part of the receipt envelope, so reject other tenants
            # before schema/freshness validation. An expired or legacy receipt
            # from another business must not poison this scope's manifest.
            if isinstance(receipt, dict) and receipt.get("scope") != scope:
                other_scope_receipts.append(
                    {
                        "path": str(path),
                        "receiptId": receipt.get("receiptId"),
                        "scope": receipt.get("scope"),
                    }
                )
                continue
            valid, reasons = validate_receipt(receipt, now=observed_now)
            receipt_id = receipt.get("receiptId") if isinstance(receipt, dict) else None
            if isinstance(receipt_id, str) and receipt_id in seen_ids:
                valid = False
                reasons = [*reasons, "duplicate_receipt_id"]
            if isinstance(receipt_id, str):
                seen_ids.add(receipt_id)
            observed_at = _parse_time(receipt.get("observedAt")) if isinstance(receipt, dict) else None
            if not valid or observed_at is None:
                invalid_receipts.append({"path": str(path), "receiptId": receipt_id, "reasons": reasons})
                continue
            valid_receipts.append((observed_at, receipt, path))

    gates = []
    selected: list[dict[str, Any]] = []
    expiries: list[datetime] = []
    for gate_id in RECOVERY_GATE_IDS:
        candidates = [item for item in valid_receipts if item[1].get("gateId") == gate_id]
        if not candidates:
            gates.append({"id": gate_id, "status": "unknown", "evidence": []})
            continue
        _, receipt, path = max(candidates, key=lambda item: item[0])
        reference = _receipt_reference(receipt, path, output.parent)
        gates.append({"id": gate_id, "status": receipt["status"], "evidence": [reference]})
        selected.append({"gateId": gate_id, "receiptId": receipt["receiptId"], "status": receipt["status"]})
        expires_at = _parse_time(receipt.get("expiresAt"))
        if expires_at is not None:
            expiries.append(expires_at)
    maximum_expiry = observed_now + timedelta(days=DEFAULT_RECOVERY_EVIDENCE_MAX_AGE_DAYS)
    expires_at = min([maximum_expiry, *expiries]) if expiries else maximum_expiry
    manifest = {
        "schemaVersion": RECOVERY_SCHEMA_VERSION,
        "scope": scope,
        "generatedAt": _iso(observed_now),
        "expiresAt": _iso(expires_at),
        "receiptSet": {
            "validCount": len(valid_receipts),
            "invalidCount": len(invalid_receipts),
        },
        "gates": gates,
    }
    diagnostics = {
        "validReceiptCount": len(valid_receipts),
        "invalidReceiptCount": len(invalid_receipts),
        "ignoredOtherScopeCount": len(other_scope_receipts),
        "ignoredOtherScopeReceipts": other_scope_receipts,
        "invalidReceipts": invalid_receipts,
        "selected": selected,
    }
    if write:
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_name(f".{output.name}.tmp-{os.getpid()}")
        temporary.write_text(json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=True) + "\n", encoding="utf-8")
        temporary.replace(output)
    return manifest, diagnostics


def collect_restore_drill(
    *,
    log_path: Path,
    receipts_dir: Path,
    scope: str = "prepende-operations",
    now: datetime | None = None,
) -> tuple[dict[str, Any], Path]:
    source = log_path.expanduser().resolve()
    lines = [line for line in source.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not lines:
        raise ValueError(f"restore drill log is empty: {source}")
    latest_raw = lines[-1].encode("utf-8")
    latest = json.loads(latest_raw.decode("utf-8"))
    if not isinstance(latest, dict):
        raise ValueError("latest restore drill receipt is not an object")
    checks = latest.get("checks") if isinstance(latest.get("checks"), dict) else {}
    observed_at = _parse_time(latest.get("ts"))
    if observed_at is None:
        raise ValueError("latest restore drill receipt has no valid ts")
    observed_now = now or datetime.now(timezone.utc)
    expires_at = min(
        observed_at + timedelta(days=DEFAULT_RECOVERY_EVIDENCE_MAX_AGE_DAYS),
        observed_now + timedelta(days=DEFAULT_RECOVERY_EVIDENCE_MAX_AGE_DAYS),
    )
    mapped_checks = [
        {"id": "backup_available", "status": "pass" if latest.get("snapshot") else "fail", "detail": f"snapshot={latest.get('snapshot') or 'missing'}"},
        {"id": "memory_restored", "status": "pass" if checks.get("memory_count", {}).get("pass") is True else "fail", "detail": "Restored memory database opened and count was within tolerance."},
        {"id": "vault_restored", "status": "pass" if checks.get("vault_pages", {}).get("pass") is True else "fail", "detail": "Restored vault page count was within tolerance."},
        {"id": "rag_rebuilt", "status": "pass" if checks.get("rag_lexical", {}).get("pass") is True else "fail", "detail": "Lexical RAG rebuilt and answered the canned queries."},
    ]
    observation = {
        "schemaVersion": OBSERVATION_SCHEMA_VERSION,
        "gateId": "prepende_recovery",
        "scope": scope,
        "proofClass": GATE_POLICIES["prepende_recovery"]["proofClass"],
        "observedAt": _iso(observed_at),
        "expiresAt": _iso(expires_at),
        "producer": {"id": "scripts/restore_drill.py", "version": "1", "kind": "automated_collector"},
        "summary": "Imported the latest isolated local Prepende restore drill without rerunning or mutating the live brain.",
        "checks": mapped_checks,
        "artifacts": [
            {
                "id": "restore-drill-jsonl-entry",
                "locator": f"{source}#line={len(lines)}",
                "digest": digest_bytes(latest_raw),
                "bytes": len(latest_raw),
            }
        ],
        "safety": {
            "isolation": "temporary_local",
            "productionMutated": False,
            "secretsStored": False,
            "externalActions": [],
        },
    }
    receipt = build_receipt(
        observation,
        source_locator=f"{source}#line={len(lines)}",
        source_digest=digest_bytes(latest_raw),
        source_bytes=len(latest_raw),
        now=observed_now,
    )
    return receipt, write_receipt(receipt, receipts_dir)
