"""Unified, read-only operational status for the Prepende system.

The collector deliberately keeps the private brain, public Protocol v2, and
trust-service repositories separate.  Repository locations are accepted only
from explicit flags or their corresponding environment variables; sibling
directories are never discovered.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from operations.continuity import load_recovery_evaluation, repository_snapshot
from prepende_brain.env import brand_env


SCHEMA_VERSION = "prepende-operational-status-v1"
PROTOCOL_PROJECT = "prepende-protocol"
PROTOCOL_VERSION = "0.3.0rc1"
PROTOCOL_REPOSITORY_NAME = "prepende"
TRUST_REPOSITORY_NAME = "prepende-trust-services"
MINIMUM_CALIBRATION_N = 30
STATUSES = frozenset(
    {"ready", "degraded", "blocked", "unknown", "notConfigured", "notApplicable"}
)
UNREADY = frozenset({"degraded", "blocked", "unknown", "notConfigured"})


class UnsafeRepositoryError(ValueError):
    """An explicitly configured path is not the expected repository."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _run(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    timeout: float = 8.0,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            list(command),
            cwd=cwd,
            env=dict(env) if env is not None else None,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def _git(root: Path, *args: str) -> str | None:
    result = _run(("git", "-C", str(root), *args), timeout=3.0)
    if result is None or result.returncode != 0:
        return None
    return result.stdout.strip()


def _remote_slug(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip().removesuffix(".git").rstrip("/")
    ssh_prefix = "git" + chr(64) + "github.com:"
    if normalized.startswith(ssh_prefix):
        return normalized.split(":", 1)[1]
    marker = "github.com/"
    if marker in normalized:
        return normalized.split(marker, 1)[1]
    return None


def _configured_path(
    explicit: str | None,
    environment_name: str,
    environment: Mapping[str, str],
) -> tuple[Path | None, str]:
    raw = explicit if explicit is not None else environment.get(environment_name, "").strip()
    if not raw:
        return None, "unconfigured"
    return Path(raw).expanduser().resolve(strict=False), "cli" if explicit is not None else "environment"


def _repository_identity(root: Path, expected_name: str) -> dict[str, Any]:
    top = _git(root, "rev-parse", "--show-toplevel")
    remote = _remote_slug(_git(root, "remote", "get-url", "origin"))
    if top is None or Path(top).resolve(strict=False) != root.resolve(strict=False):
        raise UnsafeRepositoryError("configured path is not a repository root")
    if remote is None or remote.rsplit("/", 1)[-1] != expected_name:
        raise UnsafeRepositoryError("configured repository identity does not match the required source")
    return {
        "repository": expected_name,
        "head": _git(root, "rev-parse", "HEAD"),
        "branch": _git(root, "branch", "--show-current") or None,
        "dirtyEntries": len((_git(root, "status", "--porcelain=v1") or "").splitlines()),
    }


def _configured_file(root: Path, environment_name: str, default: str) -> Path:
    raw = os.environ.get(environment_name, "").strip() or default
    path = Path(raw).expanduser()
    return path.resolve(strict=False) if path.is_absolute() else (root / path).resolve(strict=False)


def _source_snapshot(vault: Path) -> dict[str, tuple[int, int, str]]:
    snapshot: dict[str, tuple[int, int, str]] = {}
    for directory in ("wiki", "raw"):
        base = vault / directory
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.md")):
            if not path.is_file() or path.is_symlink():
                continue
            payload = path.read_bytes()
            stat = path.stat()
            relative = path.relative_to(vault).as_posix()
            snapshot[relative] = (
                int(stat.st_mtime_ns),
                len(payload),
                hashlib.sha256(payload).hexdigest(),
            )
    return snapshot


def _read_index(index_path: Path) -> dict[str, Any]:
    if not index_path.is_file():
        return {"available": False, "reason": "index_missing"}
    wal = Path(str(index_path) + "-wal")
    if wal.is_file() and wal.stat().st_size > 0:
        return {"available": False, "reason": "index_has_uncheckpointed_wal"}
    try:
        connection = sqlite3.connect(
            f"file:{index_path.as_posix()}?mode=ro&immutable=1",
            uri=True,
            timeout=1.0,
        )
        connection.row_factory = sqlite3.Row
        with connection:
            rows = connection.execute(
                "SELECT path,mtime_ns,size,content_hash FROM source_files"
            ).fetchall()
            chunks = int(connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0])
            embedded = int(
                connection.execute(
                    "SELECT COUNT(*) FROM chunks WHERE embedding IS NOT NULL"
                ).fetchone()[0]
            )
        connection.close()
    except (OSError, sqlite3.Error):
        return {"available": False, "reason": "index_unreadable"}
    indexed = {
        str(row["path"]): (
            int(row["mtime_ns"]),
            int(row["size"]),
            str(row["content_hash"]),
        )
        for row in rows
    }
    return {
        "available": True,
        "sources": indexed,
        "chunks": chunks,
        "embedded": embedded,
    }


def _graph_status(root: Path, vault: Path) -> dict[str, Any]:
    graph_path = _configured_file(root, "GRAPHIFY_GRAPH", "graphify-out/graph.json")
    if not graph_path.is_file():
        return {"status": "degraded", "reason": "graph_missing"}
    try:
        from knowledge.graphify import GraphifyProjection

        report = GraphifyProjection(str(graph_path), expected_root=str(vault)).status()
    except Exception:
        return {"status": "unknown", "reason": "graph_status_unavailable"}
    return {
        "status": "ready" if bool(report.get("ready")) else "degraded",
        "reason": report.get("reason") or (None if report.get("ready") else "unavailable"),
    }


def _collect_brain(root: Path, scope: str, _python: Path) -> dict[str, Any]:
    vault = _configured_file(root, "VAULT_PATH", "vault")
    memory_db = _configured_file(root, "MEMORY_DB", ".engram/memory.db")
    default_index = str(memory_db.parent / "vault_index.db")
    index_path = _configured_file(root, "VAULT_INDEX_PATH", default_index)
    try:
        discovered_sources = _source_snapshot(vault)
        index = _read_index(index_path)
    except OSError:
        discovered_sources = {}
        index = {"available": False, "reason": "knowledge_source_unreadable"}
    indexed_sources = index.get("sources") if isinstance(index.get("sources"), dict) else {}
    discovered = len(discovered_sources)
    indexed = len(indexed_sources)
    stale = not bool(index.get("available")) or discovered_sources != indexed_sources
    chunks = int(index.get("chunks", 0) or 0)
    embedded = int(index.get("embedded", 0) or 0)
    lexical_ready = discovered > 0 and chunks > 0 and not stale
    all_indexed = discovered > 0 and discovered == indexed
    brain_ready = lexical_ready and all_indexed
    return {
        "status": "ready" if brain_ready else "blocked",
        "scope": scope,
        "continuityAvailable": brain_ready,
        "planningAvailable": brain_ready,
        "knowledge": {
            "status": "ready" if brain_ready else "blocked",
            "discoveredSources": discovered,
            "indexedSources": indexed,
            "chunks": chunks,
            "embeddedChunks": embedded,
            "missingEmbeddings": max(0, chunks - embedded),
            "lexicalReady": lexical_ready,
            "semanticReady": chunks > 0 and embedded == chunks and not stale,
            "stale": stale,
            "reason": None if index.get("available") else index.get("reason"),
        },
        "graphify": _graph_status(root, vault),
        "connectors": {
            "status": "notConfigured",
            "available": 0,
            "ready": 0,
            "reason": "offline_probe_disabled",
        },
    }


def build_fast_context_status(*, root: Path, scope: str) -> dict[str, Any]:
    """Return the model-free subset consumed by ``context-fast``.

    This keeps the fast lane on direct, read-only inspection instead of
    constructing the full kernel composition root just to obtain status.
    """

    brain = _collect_brain(root, scope, Path(sys.executable))
    knowledge = brain["knowledge"]
    graphify = brain["graphify"]
    connectors = brain["connectors"]
    return {
        "scope": scope,
        "model": "skipped",
        "knowledge": {
            "pages": int(knowledge.get("discoveredSources", 0) or 0),
            "titles": [],
            "rag": {
                "source_files": int(knowledge.get("discoveredSources", 0) or 0),
                "indexed_files": int(knowledge.get("indexedSources", 0) or 0),
                "chunks": int(knowledge.get("chunks", 0) or 0),
                "embedded_chunks": int(knowledge.get("embeddedChunks", 0) or 0),
                "missing_embeddings": int(knowledge.get("missingEmbeddings", 0) or 0),
                "lexical_ready": bool(knowledge.get("lexicalReady")),
                "semantic_ready": bool(knowledge.get("semanticReady")),
                "stale": bool(knowledge.get("stale")),
            },
            "graphify": {
                "ready": graphify.get("status") == "ready",
                "reason": graphify.get("reason"),
            },
        },
        "connectors": {
            "tools": int(connectors.get("available", 0) or 0),
            "ready": int(connectors.get("ready", 0) or 0),
        },
        "fastLane": {
            "modelCall": False,
            "liveProviderCall": False,
        },
    }


def _collect_protocol(path: Path | None, configured_by: str, private_root: Path) -> dict[str, Any]:
    embedded = private_root / "prepende"
    embedded_version = None
    init_path = embedded / "__init__.py"
    if init_path.is_file():
        try:
            for line in init_path.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("__version__") and "=" in line:
                    embedded_version = line.split("=", 1)[1].strip().strip("\"'")
                    break
        except OSError:
            embedded_version = "unknown"
    embedded_report = {
        "status": "degraded" if embedded_version else "notApplicable",
        "version": embedded_version,
        "authority": "nonAuthoritative",
        "canSatisfyProtocolV2": False,
    }
    if path is None:
        return {
            "status": "notConfigured",
            "authoritative": None,
            "embedded": embedded_report,
        }
    identity = _repository_identity(path, PROTOCOL_REPOSITORY_NAME)
    metadata_path = path / "pyproject.toml"
    ledger_path = path / "prepende" / "ledger.py"
    if not metadata_path.is_file() or not ledger_path.is_file():
        raise UnsafeRepositoryError("configured Protocol repository is missing required files")
    try:
        metadata = tomllib.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise UnsafeRepositoryError("configured Protocol metadata is invalid") from exc
    project = metadata.get("project") if isinstance(metadata, dict) else {}
    project = project if isinstance(project, dict) else {}
    if project.get("name") != PROTOCOL_PROJECT:
        raise UnsafeRepositoryError("configured repository is not prepende-protocol")
    version = str(project.get("version", ""))
    if not version.startswith("0.3."):
        raise UnsafeRepositoryError("configured Protocol repository is not Protocol v2")
    return {
        "status": "ready",
        "authoritative": {
            **identity,
            "project": PROTOCOL_PROJECT,
            "version": version,
            "configuredBy": configured_by,
            "protocol": "v2",
        },
        "embedded": embedded_report,
    }


def _collect_trust(path: Path | None, configured_by: str) -> dict[str, Any]:
    if path is None:
        return {"status": "notConfigured"}
    identity = _repository_identity(path, TRUST_REPOSITORY_NAME)
    required = (
        path / "authority.py",
        path / "trust" / "keys.json",
        path / "pilot" / "operational_status.py",
    )
    if not all(item.is_file() for item in required):
        raise UnsafeRepositoryError("configured trust repository is missing required authority or pilot files")
    return {
        "status": "ready",
        **identity,
        "configuredBy": configured_by,
        "authoritySeparation": "anchorAndResolverDistinct",
    }


def _isolated_pilot_verification(protocol_root: Path, trust_root: Path, python: Path) -> dict[str, Any]:
    """Verify with only the configured Protocol v2 and trust repository importable."""

    program = r'''
import importlib.util, json, pathlib, sys
protocol = pathlib.Path(sys.argv[1]).resolve()
trust = pathlib.Path(sys.argv[2]).resolve()
sys.path[:0] = [str(protocol), str(trust)]
adapter_path = trust / "pilot" / "operational_status.py"
spec = importlib.util.spec_from_file_location("prepende_status_adapter", adapter_path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
safe = module.collect(trust)
print(json.dumps(safe, sort_keys=True, separators=(",", ":")))
'''
    result = _run(
        (str(python), "-I", "-c", program, str(protocol_root), str(trust_root)),
        cwd=trust_root,
        timeout=20.0,
        env={"PATH": os.environ.get("PATH", "")},
    )
    if result is None or result.returncode != 0:
        return {"status": "unknown", "reason": "isolated_protocol_verification_failed"}
    try:
        payload = json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError):
        return {"status": "unknown", "reason": "isolated_protocol_verification_invalid"}
    return payload if isinstance(payload, dict) else {"status": "unknown", "reason": "isolated_protocol_verification_invalid"}


def _collect_pilot(
    protocol_path: Path | None,
    trust_path: Path | None,
    python: Path,
) -> dict[str, Any]:
    if protocol_path is None or trust_path is None:
        return {"status": "notConfigured", "evidenceClass": "commissioning"}
    adapter = _isolated_pilot_verification(protocol_path, trust_path, python)
    if adapter.get("status") == "unknown":
        return {**adapter, "evidenceClass": "commissioning"}
    verification = adapter.get("verification") if isinstance(adapter.get("verification"), dict) else {}
    projection = adapter.get("projection") if isinstance(adapter.get("projection"), dict) else {}
    if not verification or not projection:
        return {
            "status": "blocked",
            "reason": "pilot_projection_missing",
            "evidenceClass": "commissioning",
        }
    counts = verification.get("counts") if isinstance(verification.get("counts"), dict) else {}
    projected_counts = projection.get("counts") if isinstance(projection.get("counts"), dict) else {}
    terminal = sum(int(counts.get(key, 0) or 0) for key in ("resolved", "forfeited", "void"))
    expected_counts = {
        "contracts": int(counts.get("contracts", 0) or 0),
        "resolved": int(counts.get("resolved", 0) or 0),
        "forfeited": int(counts.get("forfeited", 0) or 0),
        "void": int(counts.get("void", 0) or 0),
        "open": int(counts.get("contracts", 0) or 0) - terminal,
    }
    projected_final = projection.get("finalVerification") if isinstance(projection.get("finalVerification"), dict) else {}
    expected_final = {
        "status": verification.get("status"),
        "anchored": verification.get("anchored"),
        "independentlyResolved": verification.get("independentlyResolved"),
        "internallyValid": verification.get("internallyValid"),
        "completeThrough": verification.get("completeThrough"),
        "rowCount": verification.get("rowCount"),
    }
    projection_matches = all(projected_counts.get(key) == value for key, value in expected_counts.items())
    projection_matches = projection_matches and all(
        projected_final.get(key) == value for key, value in expected_final.items()
    )
    clean = (
        verification.get("status") == "OK"
        and verification.get("anchored") is True
        and verification.get("internallyValid") is True
        and int(verification.get("errorCount", 0) or 0) == 0
        and int(verification.get("warningCount", 0) or 0) == 0
        and int(verification.get("unwitnessedTerminalCount", 0) or 0) == 0
        and int(verification.get("untrustedResolutionCount", 0) or 0) == 0
    )
    resolved = expected_counts["resolved"]
    status = "ready" if clean and projection_matches else "blocked"
    return {
        "status": status,
        "evidenceClass": "commissioning",
        "minimumCalibrationN": MINIMUM_CALIBRATION_N,
        "calibrationEligible": resolved >= MINIMUM_CALIBRATION_N,
        "autonomyIncreaseJustified": False,
        "projectionMatchesLedger": projection_matches,
        "verification": verification,
        "counts": expected_counts,
        "reason": None if status == "ready" else (
            "pilot_projection_mismatch" if not projection_matches else "pilot_ledger_verification_failed"
        ),
        "_remoteArtifactPath": adapter.get("remoteArtifactPath"),
    }


def _collect_recovery(root: Path) -> dict[str, Any]:
    evaluation, _source = load_recovery_evaluation(root)
    gates = evaluation.get("gates") if isinstance(evaluation.get("gates"), list) else []
    gate_status = {
        str(item.get("id")): str(item.get("status"))
        for item in gates
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    return {
        "status": "ready" if evaluation.get("proven") else "blocked",
        "proven": bool(evaluation.get("proven")),
        "gateCounts": evaluation.get("gateCounts", {}),
        "credentialRecovery": gate_status.get("credential_recovery", "unknown"),
        "lostMachineRecovery": gate_status.get("lost_machine_drill", "unknown"),
    }


def _github_json(endpoint: str) -> dict[str, Any] | list[Any] | None:
    result = _run(("gh", "api", endpoint), timeout=8.0)
    if result is None or result.returncode != 0:
        return None
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, (dict, list)) else None


def _collect_online(
    enabled: bool,
    protocol: dict[str, Any],
    trust: dict[str, Any],
    protocol_path: Path | None,
    trust_path: Path | None,
    pilot_artifact_path: str | None,
) -> dict[str, Any]:
    if not enabled:
        return {"status": "notApplicable", "enabled": False}
    protocol_remote = _remote_slug(
        _git(protocol_path, "remote", "get-url", "origin") if protocol_path else None
    )
    trust_remote = _remote_slug(
        _git(trust_path, "remote", "get-url", "origin") if trust_path else None
    )
    if not protocol_remote or not trust_remote:
        return {"status": "unknown", "enabled": True, "reason": "online_repository_unconfigured"}
    release = _github_json(f"repos/{protocol_remote}/releases/tags/v{PROTOCOL_VERSION}")
    protocol_head = _github_json(f"repos/{protocol_remote}/commits/main")
    trust_head = _github_json(f"repos/{trust_remote}/commits/main")
    workflows = _github_json(f"repos/{trust_remote}/actions/workflows")
    pilot_artifact = (
        _github_json(f"repos/{trust_remote}/contents/{pilot_artifact_path}?ref=main")
        if pilot_artifact_path
        else None
    )
    checks = {
        "protocolRelease": isinstance(release, dict),
        "protocolRemoteHead": isinstance(protocol_head, dict),
        "trustRemoteHead": isinstance(trust_head, dict),
        "trustWorkflowState": isinstance(workflows, dict),
        "remotePilotArtifact": isinstance(pilot_artifact, dict),
    }
    unknown = [name for name, ok in checks.items() if not ok]
    exact_heads: dict[str, bool | None] = {"protocol": None, "trust": None}
    if isinstance(protocol_head, dict):
        local = protocol.get("authoritative") if isinstance(protocol.get("authoritative"), dict) else {}
        exact_heads["protocol"] = protocol_head.get("sha") == local.get("head")
    if isinstance(trust_head, dict):
        exact_heads["trust"] = trust_head.get("sha") == trust.get("head")
    assets = []
    if isinstance(release, dict) and isinstance(release.get("assets"), list):
        allowed = {
            f"prepende_protocol-{PROTOCOL_VERSION}-py3-none-any.whl",
            f"prepende_protocol-{PROTOCOL_VERSION}.tar.gz",
            "SHA256SUMS",
        }
        assets = sorted(
            str(item.get("name"))
            for item in release["assets"]
            if isinstance(item, dict) and item.get("name") in allowed
        )
    release_complete = len(assets) == 3
    local_heads_exact = all(value is True for value in exact_heads.values())
    status = "unknown" if unknown else ("ready" if release_complete and local_heads_exact else "degraded")
    return {
        "status": status,
        "enabled": True,
        "checks": checks,
        "releaseAssets": assets,
        "releaseComplete": release_complete,
        "exactRemoteHeads": exact_heads,
    }


def build_operational_status(
    *,
    root: Path,
    scope: str,
    protocol_repo: str | None,
    trust_repo: str | None,
    online: bool,
    environment: Mapping[str, str] | None = None,
    python: Path | None = None,
) -> tuple[dict[str, Any], int]:
    env = environment if environment is not None else os.environ
    protocol_path, protocol_source = _configured_path(
        protocol_repo, "PREPENDE_PROTOCOL_REPO", env
    )
    trust_path, trust_source = _configured_path(trust_repo, "PREPENDE_TRUST_REPO", env)
    repository_python = root / ".venv" / "bin" / "python3"
    interpreter = python or (repository_python if repository_python.is_file() else Path(sys.executable))
    try:
        brain = _collect_brain(root, scope, interpreter)
        protocol = _collect_protocol(protocol_path, protocol_source, root)
        trust = _collect_trust(trust_path, trust_source)
        pilot = _collect_pilot(protocol_path, trust_path, interpreter)
        recovery = _collect_recovery(root)
        pilot_artifact_path = pilot.pop("_remoteArtifactPath", None)
        online_status = _collect_online(
            online,
            protocol,
            trust,
            protocol_path,
            trust_path,
            str(pilot_artifact_path) if pilot_artifact_path else None,
        )
    except UnsafeRepositoryError as exc:
        payload = {
            "schemaVersion": SCHEMA_VERSION,
            "status": "blocked",
            "error": "unsafe_repository_identity",
            "detail": str(exc),
        }
        return payload, 2
    sections = {
        "brain": brain,
        "protocol": protocol,
        "trust": trust,
        "pilot": pilot,
        "recovery": recovery,
        "online": online_status,
    }
    section_statuses = [
        value.get("status")
        for value in sections.values()
        if isinstance(value, dict) and value.get("status") != "notApplicable"
    ]
    overall = "ready" if section_statuses and not any(status in UNREADY for status in section_statuses) else "degraded"
    payload = {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": _utc_now(),
        "status": overall,
        "mode": "online-read-only" if online else "offline-read-only",
        "scope": scope,
        **sections,
        "sources": {
            "brain": {"configuredBy": "repository"},
            "protocol": {"configuredBy": protocol_source},
            "trust": {"configuredBy": trust_source},
            "repository": repository_snapshot(root),
        },
        "safety": {
            "modelCall": False,
            "connectorProbe": False,
            "indexRebuild": False,
            "workflowDispatch": False,
            "memoryWrite": False,
        },
    }
    # Local paths are useful internally but not part of the public status contract.
    repository = payload["sources"]["repository"]
    if isinstance(repository, dict):
        repository.pop("path", None)
    return payload, 0 if overall == "ready" else 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="prepende operational-status", add_help=False)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--scope", default=None)
    parser.add_argument("--protocol-repo")
    parser.add_argument("--trust-repo")
    parser.add_argument("--online", action="store_true")
    parser.add_argument("-h", "--help", action="help")
    return parser


def main(argv: list[str] | None = None, *, root: Path | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:
        return int(exc.code)
    scope = args.scope or brand_env("SCOPE", "default") or "default"
    payload, code = build_operational_status(
        root=(root or Path(__file__).resolve().parents[1]),
        scope=str(scope),
        protocol_repo=args.protocol_repo,
        trust_repo=args.trust_repo,
        online=bool(args.online),
    )
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"Prepende operational status: {payload.get('status', 'unknown')}")
        if payload.get("schemaVersion") == SCHEMA_VERSION:
            for name in ("brain", "protocol", "trust", "pilot", "recovery", "online"):
                section = payload.get(name)
                status = section.get("status", "unknown") if isinstance(section, dict) else "unknown"
                print(f"  {name}: {status}")
        elif payload.get("detail"):
            print(f"  {payload['detail']}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
