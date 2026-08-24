"""Fast, read-only continuity and recovery verdicts for Prepende.

The fast lane must never depend on a model or a live provider.  Expensive checks
write receipts elsewhere; this module only inspects local repository state,
operator receipts, and an optional cached recovery manifest.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


PACKET_SCHEMA_VERSION = "prepende-context-fast-v2"
CONTINUITY_SCHEMA_VERSION = "prepende-continuity-v2"
RECOVERY_SCHEMA_VERSION = "prepende-recovery-manifest-v1"
DEFAULT_PACKET_TTL_SECONDS = 300
DEFAULT_RECOVERY_EVIDENCE_MAX_AGE_DAYS = 31
DEFAULT_RECOVERY_MANIFEST = Path(".engram/continuity/recovery-manifest.json")
DEFAULT_SCOPED_RECOVERY_MANIFEST_PREFIX = "recovery-manifest-"
SUPPORTED_PROFILES = frozenset({"general", "coding", "deployment", "recovery"})
RAG_COUNT_FIELDS = (
    "source_files",
    "indexed_files",
    "chunks",
    "embedded_chunks",
    "missing_embeddings",
)
_GIT_REMOTE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
_GIT_BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")

RECOVERY_GATE_IDS = (
    "inventory",
    "source_recovery",
    "work_in_progress_recovery",
    "prepende_recovery",
    "assistant_continuity",
    "netlify_recovery",
    "supabase_recovery",
    "credential_recovery",
    "lost_machine_drill",
    "failure_detection",
)


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


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


def _git(root: Path, *args: str) -> str | None:
    env = os.environ.copy()
    env["GIT_OPTIONAL_LOCKS"] = "0"
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=1.0,
            check=False,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def repository_snapshot(root: Path) -> dict[str, Any]:
    """Return local-only repository provenance without contacting a remote."""

    top = _git(root, "rev-parse", "--show-toplevel")
    if not top:
        return {"available": False, "path": str(root), "observedAt": _iso(datetime.now(timezone.utc))}
    branch = _git(root, "branch", "--show-current") or None
    head = _git(root, "rev-parse", "HEAD")
    status = _git(root, "status", "--porcelain=v1")
    upstream = _git(root, "rev-parse", "--abbrev-ref", "@{upstream}")
    upstream_head = _git(root, "rev-parse", "@{upstream}") if upstream else None
    remotes = _git(root, "remote")
    dirty_entries = len(status.splitlines()) if status else 0
    return {
        "available": bool(head),
        "path": top,
        "branch": branch,
        "head": head,
        "dirtyEntries": dirty_entries,
        "upstream": upstream,
        "upstreamHead": upstream_head,
        "headMatchesUpstream": bool(head and upstream_head and head == upstream_head),
        "remoteConfigured": bool(remotes),
        "observedAt": _iso(datetime.now(timezone.utc)),
    }


def verify_remote_revision(
    root: Path,
    *,
    base_ref: str = "origin/main",
) -> dict[str, Any]:
    """Read the configured Git remote and prove HEAD matches its branch tip.

    This intentionally uses ``ls-remote`` rather than trusting a possibly stale
    local tracking ref. It does not fetch objects or mutate the checkout.
    """

    observed_at = _iso(datetime.now(timezone.utc))
    if "/" not in base_ref or base_ref.startswith("/") or base_ref.endswith("/"):
        return {
            "expectedBaseRef": base_ref,
            "remoteVerified": False,
            "headMatchesRemote": False,
            "remoteVerificationReason": "expected_base_ref_invalid",
            "remoteObservedAt": observed_at,
        }
    remote, branch = base_ref.split("/", 1)
    if (
        not _GIT_REMOTE_RE.fullmatch(remote)
        or not _GIT_BRANCH_RE.fullmatch(branch)
        or ".." in branch
        or "@{" in branch
        or any(part in {"", ".", ".."} for part in branch.split("/"))
    ):
        return {
            "expectedBaseRef": base_ref,
            "remoteVerified": False,
            "headMatchesRemote": False,
            "remoteVerificationReason": "expected_base_ref_invalid",
            "remoteObservedAt": observed_at,
        }
    head = _git(root, "rev-parse", "HEAD")
    local_base = _git(root, "rev-parse", base_ref)
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "ls-remote", "--exit-code", remote, f"refs/heads/{branch}"],
            capture_output=True,
            text=True,
            timeout=15.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        proc = None
    remote_head = None
    if proc is not None and proc.returncode == 0:
        fields = proc.stdout.strip().split()
        if len(fields) >= 2 and fields[1] == f"refs/heads/{branch}":
            remote_head = fields[0]
    verified = bool(
        head
        and local_base
        and remote_head
        and local_base == remote_head
    )
    return {
        "expectedBaseRef": base_ref,
        "localBaseHead": local_base,
        "remoteHead": remote_head,
        "remoteVerified": verified,
        "headMatchesRemote": bool(verified and head == remote_head),
        "remoteVerificationReason": None if verified else "remote_revision_unverified",
        "remoteObservedAt": observed_at,
    }


def _latest_operator_receipt(root: Path, scope: str, goal_hash: str) -> dict[str, Any] | None:
    receipts_dir = root / ".engram" / "operator-receipts"
    if not receipts_dir.is_dir():
        return None
    candidates: list[tuple[float, dict[str, Any]]] = []
    for path in receipts_dir.glob("op_*.json"):
        if path.name.endswith("-sandbox-output.json"):
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if payload.get("scope") != scope:
            continue
        try:
            observed = path.stat().st_mtime
        except OSError:
            observed = 0.0
        candidates.append((observed, payload))
    if not candidates:
        return None
    payload = max(
        candidates,
        key=lambda item: (item[1].get("goalHash") == goal_hash, item[0]),
    )[1]
    learning = payload.get("learning") if isinstance(payload.get("learning"), dict) else {}
    return {
        "receiptId": payload.get("receiptId"),
        "status": payload.get("status"),
        "goalHash": payload.get("goalHash"),
        "matchesGoal": payload.get("goalHash") == goal_hash,
        "startedAt": payload.get("startedAt"),
        "completedAt": payload.get("completedAt"),
        "next": None,
        "durableMemoryWrite": bool(learning.get("durableMemoryWrite")),
        "learningStatus": learning.get("status"),
    }


def recovery_manifest_path(root: Path, scope: str | None = None) -> Path:
    raw = os.environ.get("PREPENDE_RECOVERY_MANIFEST", "").strip()
    if raw:
        path = Path(raw).expanduser()
        return path if path.is_absolute() else root / path
    if scope is not None:
        if (
            not isinstance(scope, str)
            or not scope.strip()
            or scope != scope.strip()
        ):
            raise ValueError("recovery manifest scope must be a non-empty canonical string")
        scope_hash = hashlib.sha256(scope.encode("utf-8")).hexdigest()
        return root / DEFAULT_RECOVERY_MANIFEST.parent / (
            f"{DEFAULT_SCOPED_RECOVERY_MANIFEST_PREFIX}{scope_hash}.json"
        )
    return root / DEFAULT_RECOVERY_MANIFEST


def resolve_recovery_manifest_path(root: Path, scope: str) -> Path:
    """Resolve one exact-scope cache without masking a broken scoped file."""

    scoped = recovery_manifest_path(root, scope)
    try:
        scoped.lstat()
        scoped_path_occupied = True
    except FileNotFoundError:
        scoped_path_occupied = False
    except OSError:
        scoped_path_occupied = True
    if os.environ.get("PREPENDE_RECOVERY_MANIFEST", "").strip() or scoped_path_occupied:
        return scoped
    legacy = root / DEFAULT_RECOVERY_MANIFEST
    return legacy if legacy.is_file() else scoped


def evaluate_recovery_manifest(
    manifest: Any,
    *,
    now: datetime | None = None,
    manifest_dir: Path | None = None,
    expected_scope: str | None = None,
) -> dict[str, Any]:
    """Evaluate a cached manifest.  No checks are performed against live systems."""

    observed_now = now or datetime.now(timezone.utc)
    reasons: list[str] = []
    if not isinstance(manifest, dict):
        return {
            "proven": False,
            "status": "missing",
            "reasons": ["recovery_manifest_missing"],
            "gateCounts": {"pass": 0, "fail": 0, "unknown": len(RECOVERY_GATE_IDS)},
        }
    if manifest.get("schemaVersion") != RECOVERY_SCHEMA_VERSION:
        reasons.append("recovery_manifest_schema_mismatch")
    expected_scope_valid = (
        isinstance(expected_scope, str)
        and bool(expected_scope.strip())
        and expected_scope == expected_scope.strip()
    )
    if not expected_scope_valid:
        reasons.append("recovery_expected_scope_missing")
    manifest_scope = manifest.get("scope")
    if (
        not isinstance(manifest_scope, str)
        or not manifest_scope.strip()
        or manifest_scope != manifest_scope.strip()
    ):
        reasons.append("recovery_manifest_scope_missing")
    elif expected_scope_valid and manifest_scope != expected_scope:
        reasons.append("recovery_manifest_scope_mismatch")
    receipt_set = manifest.get("receiptSet")
    if not isinstance(receipt_set, dict):
        reasons.append("recovery_receipt_set_invalid")
    else:
        invalid_count = receipt_set.get("invalidCount", 0)
        if (
            not isinstance(invalid_count, int)
            or isinstance(invalid_count, bool)
            or invalid_count < 0
            or "invalidCount" not in receipt_set
        ):
            reasons.append("recovery_receipt_set_invalid")
        elif invalid_count > 0:
            reasons.append("recovery_receipt_set_contains_invalid_receipts")
    generated_at = _parse_time(manifest.get("generatedAt"))
    expires_at = _parse_time(manifest.get("expiresAt"))
    if generated_at is None:
        reasons.append("recovery_manifest_generated_at_invalid")
    elif generated_at > observed_now + timedelta(minutes=5):
        reasons.append("recovery_manifest_generated_in_future")
    if expires_at is None:
        reasons.append("recovery_manifest_expires_at_invalid")
    elif expires_at <= observed_now:
        reasons.append("recovery_manifest_expired")

    raw_gates = manifest.get("gates")
    gates = raw_gates if isinstance(raw_gates, list) else []
    by_id = {
        gate.get("id"): gate
        for gate in gates
        if isinstance(gate, dict) and isinstance(gate.get("id"), str)
    }
    counts = {"pass": 0, "fail": 0, "unknown": 0}
    evaluated_gates = []
    if len(by_id) != len(gates):
        reasons.append("duplicate_or_invalid_recovery_gates")
    for gate_id in RECOVERY_GATE_IDS:
        gate = by_id.get(gate_id)
        status = str(gate.get("status", "unknown")).lower() if isinstance(gate, dict) else "unknown"
        evidence = gate.get("evidence", []) if isinstance(gate, dict) else []
        evidence_ok = isinstance(evidence, list) and bool(evidence)
        evidence_reasons: list[str] = []
        if evidence_ok:
            from operations.recovery_receipts import validate_receipt_reference

            for item in evidence:
                if not isinstance(item, dict):
                    evidence_ok = False
                    break
                observed_at = _parse_time(item.get("observedAt"))
                digest = item.get("digest")
                if (
                    not isinstance(item.get("receiptId"), str)
                    or not item["receiptId"].strip()
                    or observed_at is None
                    or observed_at > observed_now + timedelta(minutes=5)
                    or observed_at < observed_now - timedelta(days=DEFAULT_RECOVERY_EVIDENCE_MAX_AGE_DAYS)
                    or not isinstance(digest, str)
                    or len(digest) != 71
                    or not digest.startswith("sha256:")
                ):
                    evidence_ok = False
                    break
                reference_ok, reference_reasons = validate_receipt_reference(
                    item,
                    gate_id=gate_id,
                    manifest_dir=manifest_dir,
                    expected_scope=manifest_scope if isinstance(manifest_scope, str) else None,
                    now=observed_now,
                )
                if not reference_ok:
                    evidence_ok = False
                    evidence_reasons.extend(reference_reasons)
                    break
        if status == "pass" and evidence_ok:
            counts["pass"] += 1
            evaluated_status = "pass"
        elif status == "fail":
            counts["fail"] += 1
            evaluated_status = "fail"
            reasons.append(f"gate_failed:{gate_id}")
        else:
            counts["unknown"] += 1
            evaluated_status = "unknown"
            reasons.append(f"gate_unproven:{gate_id}")
            if status == "pass" and not evidence_ok:
                reasons.append(f"gate_evidence_invalid:{gate_id}")
                reasons.extend(f"gate_receipt_invalid:{gate_id}:{reason}" for reason in evidence_reasons)
        evaluated_gates.append({"id": gate_id, "status": evaluated_status, "evidenceCount": len(evidence) if isinstance(evidence, list) else 0})

    unexpected = sorted(set(by_id).difference(RECOVERY_GATE_IDS))
    if unexpected:
        reasons.append("unexpected_recovery_gates")
    proven = not reasons and counts["pass"] == len(RECOVERY_GATE_IDS)
    return {
        "proven": proven,
        "status": "proven" if proven else "unproven",
        "scope": manifest.get("scope"),
        "generatedAt": manifest.get("generatedAt"),
        "expiresAt": manifest.get("expiresAt"),
        "manifestDigest": _digest(manifest),
        "gateCounts": counts,
        "gates": evaluated_gates,
        "reasons": reasons,
    }


def load_recovery_evaluation(
    root: Path,
    *,
    scope: str,
    now: datetime | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    path = resolve_recovery_manifest_path(root, scope)
    source = {"id": "recovery-manifest", "path": str(path), "available": path.is_file(), "observedAt": _iso(now or datetime.now(timezone.utc))}
    if not path.is_file():
        return evaluate_recovery_manifest(None, now=now, expected_scope=scope), source
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        result = evaluate_recovery_manifest(
            {},
            now=now,
            manifest_dir=path.parent,
            expected_scope=scope,
        )
        result["reasons"] = [f"recovery_manifest_unreadable:{type(exc).__name__}"]
        return result, source
    source["digest"] = _digest(manifest)
    return evaluate_recovery_manifest(
        manifest,
        now=now,
        manifest_dir=path.parent,
        expected_scope=scope,
    ), source


def _add_blocker(
    blockers: list[dict[str, Any]],
    blocker_id: str,
    severity: str,
    detail: str,
    *,
    blocks: tuple[str, ...] = (),
    source_id: str | None = None,
) -> None:
    blockers.append(
        {
            "id": blocker_id,
            "severity": severity,
            "detail": detail,
            "blocks": list(blocks),
            "sourceId": source_id,
        }
    )


def _rag_continuity_state(rag: Any) -> str:
    """Classify the scoped RAG status without upgrading an empty index to ready."""

    if not isinstance(rag, dict) or not rag:
        return "invalid"
    counts = {field: rag.get(field) for field in RAG_COUNT_FIELDS}
    if any(
        not isinstance(value, int) or isinstance(value, bool) or value < 0
        for value in counts.values()
    ):
        return "invalid"
    if not isinstance(rag.get("stale"), bool) or not isinstance(
        rag.get("lexical_ready"), bool
    ):
        return "invalid"

    empty = all(value == 0 for value in counts.values())
    if empty:
        return "empty" if rag["stale"] is False and rag["lexical_ready"] is False else "invalid"

    internally_consistent = (
        counts["source_files"] > 0
        and counts["source_files"] == counts["indexed_files"]
        and counts["chunks"] > 0
        and counts["embedded_chunks"] <= counts["chunks"]
        and counts["embedded_chunks"] + counts["missing_embeddings"] == counts["chunks"]
    )
    if rag["lexical_ready"] is True and rag["stale"] is False and internally_consistent:
        return "ready"
    return "unavailable"


def build_continuity_packet(
    *,
    root: Path,
    goal: str,
    scope: str,
    profile: str,
    status_payload: Any,
    transport_ok: bool,
    now: datetime | None = None,
    ttl_seconds: int = DEFAULT_PACKET_TTL_SECONDS,
) -> dict[str, Any]:
    observed_now = now or datetime.now(timezone.utc)
    normalized_profile = profile if profile in SUPPORTED_PROFILES else "general"
    repository = repository_snapshot(root)
    goal_hash = "sha256:" + hashlib.sha256(goal.encode("utf-8")).hexdigest()
    checkpoint = _latest_operator_receipt(root, scope, goal_hash)
    recovery, recovery_source = load_recovery_evaluation(
        root,
        scope=scope,
        now=observed_now,
    )
    blockers: list[dict[str, Any]] = []

    if not transport_ok:
        _add_blocker(blockers, "status_transport_failed", "critical", "Prepende status did not return cleanly.", blocks=("continuity", "plan", "recovery"), source_id="prepende-status")
    if not repository.get("available"):
        _add_blocker(blockers, "repository_unavailable", "critical", "The local repository identity could not be established.", blocks=("continuity", "plan"), source_id="repository")
    elif normalized_profile in {"deployment", "recovery"} and repository.get("dirtyEntries", 0):
        _add_blocker(blockers, "repository_dirty", "critical", "The selected profile requires an exact clean source candidate.", blocks=("plan",), source_id="repository")
    if repository.get("available") and not repository.get("remoteConfigured"):
        _add_blocker(blockers, "repository_remote_missing", "warning", "No Git remote is configured for this repository.", blocks=("plan",) if normalized_profile in {"deployment", "recovery"} else (), source_id="repository")

    status = status_payload if isinstance(status_payload, dict) else {}
    knowledge = status.get("knowledge") if isinstance(status.get("knowledge"), dict) else {}
    rag = knowledge.get("rag")
    graph = knowledge.get("graphify") if isinstance(knowledge.get("graphify"), dict) else {}
    if transport_ok:
        rag_state = _rag_continuity_state(rag)
        if rag_state == "empty" and normalized_profile == "coding":
            _add_blocker(
                blockers,
                "rag_empty_coding_scope",
                "advisory",
                "The tenant knowledge index is truthfully empty; coding work may proceed without knowledge recall.",
                source_id="prepende-status",
            )
        elif rag_state == "invalid":
            _add_blocker(
                blockers,
                "rag_status_invalid",
                "critical",
                "The scoped RAG status is missing, malformed, or internally inconsistent.",
                blocks=("continuity", "plan"),
                source_id="prepende-status",
            )
        elif rag_state != "ready":
            _add_blocker(
                blockers,
                "rag_lexical_unavailable",
                "critical",
                "The rebuildable lexical knowledge path is unavailable.",
                blocks=("continuity", "plan"),
                source_id="prepende-status",
            )
    if graph and not bool(graph.get("ready")):
        _add_blocker(blockers, "graph_projection_stale", "advisory", f"Graphify is optional and currently unavailable: {graph.get('reason') or 'unknown'}.", source_id="prepende-status")
    connectors = status.get("connectors") if isinstance(status.get("connectors"), dict) else {}
    if connectors and int(connectors.get("tools", 0) or 0) > 0 and int(connectors.get("ready", 0) or 0) == 0:
        _add_blocker(blockers, "connectors_unavailable", "warning", "No configured connector is currently ready; task-specific execution may be limited.", blocks=("plan",) if normalized_profile == "deployment" else (), source_id="prepende-status")

    if not recovery.get("proven"):
        _add_blocker(
            blockers,
            "recovery_unproven",
            "critical" if normalized_profile == "recovery" else "warning",
            "A fresh ten-gate recovery manifest has not been proven.",
            blocks=("plan", "recovery") if normalized_profile == "recovery" else ("recovery",),
            source_id="recovery-manifest",
        )

    continuity_ready = transport_ok and not any("continuity" in item["blocks"] for item in blockers)
    plan_ready = continuity_ready and not any("plan" in item["blocks"] for item in blockers)
    recovery_proven = bool(recovery.get("proven")) and not any("recovery" in item["blocks"] and item["id"] != "recovery_unproven" for item in blockers)
    expires_at = observed_now + timedelta(seconds=max(1, ttl_seconds))
    sources = [
        {"id": "prepende-status", "available": isinstance(status_payload, dict), "observedAt": _iso(observed_now), "digest": _digest(status_payload) if isinstance(status_payload, dict) else None},
        {"id": "repository", "available": bool(repository.get("available")), "observedAt": repository.get("observedAt"), "digest": _digest(repository)},
        recovery_source,
    ]
    material = {
        "schemaVersion": CONTINUITY_SCHEMA_VERSION,
        "generatedAt": _iso(observed_now),
        "expiresAt": _iso(expires_at),
        "goal": goal,
        "goalHash": goal_hash,
        "scope": scope,
        "profile": normalized_profile,
        "verdict": {
            "transportOk": transport_ok,
            "continuityReady": continuity_ready,
            "planReady": plan_ready,
            "recoveryProven": recovery_proven,
        },
        "repository": repository,
        "checkpoint": checkpoint,
        "recovery": recovery,
        "blockers": blockers,
        "sources": sources,
    }
    material["packetId"] = _digest(material)
    return material
