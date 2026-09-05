"""Hermetic contract tests for ``prepende operational-status``."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from operations import operational_status as status  # noqa: E402


def _status_with_isolated_brain(
    root: Path,
    scope: str,
    protocol_repo: str | None,
    trust_repo: str | None,
    online: bool,
    environment: dict[str, object],
    python: Path,
) -> tuple[dict[str, object], int]:
    with mock.patch.dict(
        status.os.environ,
        {
            "VAULT_PATH": "vault",
            "MEMORY_SCOPE": "scope-a",
            "MEMORY_DB": ".engram/memory.db",
            "VAULT_INDEX_PATH": ".engram/vault_index.db",
            "GRAPHIFY_GRAPH": "graphify-out/graph.json",
            "GRAPHIFY_GRAPH_PATH": "graphify-out/graph.json",
        },
        clear=False,
    ):
        return status.build_operational_status(
            root=root,
            scope=scope,
            protocol_repo=protocol_repo,
            trust_repo=trust_repo,
            online=online,
            environment=environment,
            python=python,
        )


def _ready_brain(scope: str = "scope-a") -> dict:
    return {
        "status": "ready",
        "scope": scope,
        "continuityAvailable": True,
        "planningAvailable": True,
        "knowledge": {
            "status": "ready",
            "discoveredSources": 2,
            "indexedSources": 2,
            "chunks": 3,
            "lexicalReady": True,
            "semanticReady": False,
            "stale": False,
        },
        "graphify": {"status": "degraded", "reason": "root_mismatch"},
        "connectors": {"status": "notConfigured", "available": 0, "ready": 0},
    }


def _verification() -> dict:
    return {
        "status": "OK",
        "anchored": True,
        "independentlyResolved": True,
        "internallyValid": True,
        "completeThrough": 13,
        "rowCount": 16,
        "counts": {"contracts": 10, "resolved": 1, "forfeited": 0, "void": 0},
        "errorCount": 0,
        "warningCount": 0,
        "unwitnessedTerminalCount": 0,
        "untrustedResolutionCount": 0,
    }


def _state(override: dict | None = None) -> dict:
    value = {
        "counts": {"contracts": 10, "resolved": 1, "forfeited": 0, "void": 0, "open": 9},
        "finalVerification": {
            "status": "OK",
            "anchored": True,
            "independentlyResolved": True,
            "internallyValid": True,
            "completeThrough": 13,
            "rowCount": 16,
        },
    }
    if override:
        value.update(override)
    return value


def _write_state(root: Path, value: dict) -> Path:
    path = root / "pilot" / "fixture" / "state.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _git_repository(root: Path, remote: str) -> None:
    root.mkdir(parents=True)
    subprocess.run(("git", "init", "-q", str(root)), check=True)
    subprocess.run(("git", "-C", str(root), "remote", "add", "origin", remote), check=True)


def _unconfigured_never_discovers_siblings(temp: Path) -> None:
    brain = temp / "private"
    brain.mkdir(parents=True)
    (temp / "prepende").mkdir()
    with (
        mock.patch.object(status, "_collect_brain", return_value=_ready_brain()),
        mock.patch.object(status, "_collect_recovery", return_value={"status": "ready", "proven": True}),
    ):
        payload, code = _status_with_isolated_brain(
            root=brain,
            scope="scope-a",
            protocol_repo=None,
            trust_repo=None,
            online=False,
            environment={},
            python=Path(sys.executable),
        )
    assert code == 1, (code, payload)
    assert payload["protocol"]["status"] == "notConfigured", payload
    assert payload["trust"]["status"] == "notConfigured", payload
    assert payload["pilot"]["status"] == "notConfigured", payload
    assert payload["online"]["status"] == "notApplicable", payload


def _configuration_precedence(temp: Path) -> None:
    cli = temp / "cli"
    env = temp / "env"
    chosen, source = status._configured_path(str(cli), "PREPENDE_PROTOCOL_REPO", {"PREPENDE_PROTOCOL_REPO": str(env)})
    assert chosen == cli.resolve() and source == "cli", (chosen, source)
    chosen, source = status._configured_path(None, "PREPENDE_PROTOCOL_REPO", {"PREPENDE_PROTOCOL_REPO": str(env)})
    assert chosen == env.resolve() and source == "environment", (chosen, source)


def _wrong_repository_is_exit_two(temp: Path) -> None:
    wrong = temp / "wrong"
    _git_repository(wrong, "https://github.com/example/not-prepende.git")
    private = temp / "private"
    private.mkdir()
    with mock.patch.object(status, "_collect_brain", return_value=_ready_brain()):
        payload, code = _status_with_isolated_brain(
            root=private,
            scope="scope-a",
            protocol_repo=str(wrong),
            trust_repo=None,
            online=False,
            environment={},
            python=Path(sys.executable),
        )
    assert code == 2 and payload["error"] == "unsafe_repository_identity", payload


def _embedded_v02_is_never_authoritative(temp: Path) -> None:
    root = temp / "private"
    package = root / "prepende"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text('__version__ = "0.2.0"\n', encoding="utf-8")
    report = status._collect_protocol(None, "unconfigured", root)
    assert report["status"] == "notConfigured", report
    assert report["embedded"]["version"] == "0.2.0", report
    assert report["embedded"]["authority"] == "nonAuthoritative", report
    assert report["embedded"]["canSatisfyProtocolV2"] is False, report


def _pilot_projection_is_authoritatively_compared(temp: Path) -> None:
    protocol = temp / "protocol"
    protocol.mkdir(parents=True)
    trust = temp / "trust"
    state_path = _write_state(trust, _state())
    before = hashlib.sha256(state_path.read_bytes()).hexdigest()
    adapter = {
        "verification": _verification(),
        "projection": _state(),
        "remoteArtifactPath": "pilot/status.json",
    }
    with mock.patch.object(status, "_isolated_pilot_verification", return_value=adapter):
        ready = status._collect_pilot(protocol, trust, Path(sys.executable))
    after = hashlib.sha256(state_path.read_bytes()).hexdigest()
    assert ready["status"] == "ready" and ready["projectionMatchesLedger"] is True, ready
    assert ready["evidenceClass"] == "commissioning", ready
    assert ready["calibrationEligible"] is False and ready["autonomyIncreaseJustified"] is False, ready
    assert before == after, "operational status changed the pilot projection"

    mismatched = {
        **adapter,
        "projection": _state(
            {"counts": {"contracts": 10, "resolved": 10, "forfeited": 0, "void": 0, "open": 0}}
        ),
    }
    with mock.patch.object(status, "_isolated_pilot_verification", return_value=mismatched):
        blocked = status._collect_pilot(protocol, trust, Path(sys.executable))
    assert blocked["status"] == "blocked", blocked
    assert blocked["reason"] == "pilot_projection_mismatch", blocked


def _offline_brain_collection_is_byte_identical(temp: Path) -> None:
    page = temp / "vault" / "wiki" / "ready.md"
    page.parent.mkdir(parents=True)
    page.write_text("# Ready\n\nlexical status fixture\n", encoding="utf-8")
    graph = temp / "graphify-out" / "graph.json"
    graph.parent.mkdir(parents=True)
    graph.write_text('{"nodes":[], "links":[]}', encoding="utf-8")
    index = temp / ".engram" / "vault_index.db"
    index.parent.mkdir(parents=True)
    payload = page.read_bytes()
    stat = page.stat()
    with sqlite3.connect(index) as connection:
        connection.execute(
            "CREATE TABLE source_files(path TEXT PRIMARY KEY,mtime_ns INTEGER,size INTEGER,content_hash TEXT)"
        )
        connection.execute(
            "CREATE TABLE chunks(id TEXT PRIMARY KEY,embedding TEXT)"
        )
        connection.execute(
            "INSERT INTO source_files VALUES(?,?,?,?)",
            (
                "wiki/ready.md",
                stat.st_mtime_ns,
                len(payload),
                hashlib.sha256(payload).hexdigest(),
            ),
        )
        connection.execute("INSERT INTO chunks VALUES('chunk-1',NULL)")

    def tree_snapshot() -> dict[str, tuple[bytes, int, int]]:
        return {
            path.relative_to(temp).as_posix(): (
                path.read_bytes(),
                path.stat().st_mode,
                path.stat().st_mtime_ns,
            )
            for path in sorted(temp.rglob("*"))
            if path.is_file()
        }

    before = tree_snapshot()
    with mock.patch.dict(
        status.os.environ,
        {
            "VAULT_PATH": "vault",
            "MEMORY_SCOPE": "scope-a",
            "VAULT_INDEX_PATH": ".engram/vault_index.db",
            "GRAPHIFY_GRAPH": "graphify-out/graph.json",
            "GRAPHIFY_GRAPH_PATH": "graphify-out/graph.json",
        },
        clear=False,
    ):
        report = status._collect_brain(temp, "scope-a", Path(sys.executable))
    after = tree_snapshot()
    assert report["status"] == "ready", report
    assert before == after, "offline brain collector changed repository/runtime bytes"


def _scoped_corpus_coverage(temp: Path) -> None:
    import asyncio
    from knowledge.rag import VaultRagIndex
    from knowledge.scoped import tenant_vault_path
    from operations.continuity import _rag_continuity_state

    temp.mkdir(parents=True)
    base = temp / "vault"
    tenant = tenant_vault_path(base, "tenant-one")
    for vault, text in ((base, "private owner content"), (tenant, "approved tenant content")):
        (vault / "wiki").mkdir(parents=True)
        (vault / "wiki" / "source.md").write_text(text)
    env = {"VAULT_PATH": str(base), "MEMORY_DB": str(temp / "state" / "memory.db"),
           "MEMORY_SCOPE": "owner", "PREPENDE_DOTENV_POLICY": "disabled"}
    with mock.patch.dict(os.environ, env, clear=True):
        owner_index = VaultRagIndex(str(base))
        tenant_index = VaultRagIndex(str(tenant))
        asyncio.run(owner_index.refresh())
        asyncio.run(tenant_index.refresh())
        report = status.build_fast_context_status(root=temp, scope="tenant-one")
        rag = report["knowledge"]["rag"]
        coverage = report["knowledge"]["contextCoverage"]
        assert rag["source_files"] == rag["indexed_files"] == 1, report
        assert rag["chunks"] == tenant_index.status()["chunks"]
        assert _rag_continuity_state(rag) == "ready"
        assert coverage["scope"] == "tenant-one" and coverage["namespace"] == "tenant"
        assert coverage["manifestStatus"] == "notConfigured"
        assert coverage["voiceStatus"] == "notPinned"
        assert coverage["observedSha256"] == coverage["indexedSha256"]
        owner_coverage = status.build_fast_context_status(root=temp, scope="owner")["knowledge"]["contextCoverage"]
        assert owner_coverage["observedSha256"] != coverage["observedSha256"]
        assert report["knowledge"]["graphify"]["reason"] == "owner_graph_excluded_from_tenant_scope"
        absent = status.build_fast_context_status(root=temp, scope="tenant-absent")
        assert absent["knowledge"]["rag"]["source_files"] == 0
        assert not absent["knowledge"]["rag"]["lexical_ready"]
        assert not (base / "tenants" / "tenant-absent").exists(), "read-only status created a vault"
        (base / "tenants" / "tenant-alias").symlink_to(tenant, target_is_directory=True)
        try:
            status.build_fast_context_status(root=temp, scope="tenant-alias")
        except ValueError:
            pass
        else:
            raise AssertionError("sibling tenant symlink relabeled another corpus")
        try:
            status.build_fast_context_status(root=temp, scope="../owner")
        except ValueError:
            pass
        else:
            raise AssertionError("invalid scope accepted")

        manifest = {"schemaVersion": "prepende-corpus-manifest-v1", "scope": "tenant-one",
                    "revision": "v1", "approvalRef": "fixture-owner-approval",
                    "sources": [{"path": "wiki/source.md", "purpose": "voice",
                                 "sha256": hashlib.sha256(b"approved tenant content").hexdigest()}]}
        path = temp / "manifest.json"
        path.write_text(json.dumps(manifest))
        os.environ["PREPENDE_CORPUS_MANIFEST"] = str(path)
        matched = status.build_fast_context_status(root=temp, scope="tenant-one")
        assert matched["knowledge"]["contextCoverage"]["manifestStatus"] == "matched"
        assert matched["knowledge"]["contextCoverage"]["voiceStatus"] == "matched"
        (tenant / "wiki" / "source.md").write_text("edited without approval")
        asyncio.run(tenant_index.refresh())
        changed = status.build_fast_context_status(root=temp, scope="tenant-one")
        assert changed["knowledge"]["contextCoverage"]["changedSources"] == 1
        assert changed["knowledge"]["contextCoverage"]["voiceStatus"] == "mismatch"
        assert _rag_continuity_state(changed["knowledge"]["rag"]) == "corpus_mismatch"
        assert not changed["knowledge"]["ready"]
        (tenant / "wiki" / "source.md").unlink()
        (tenant / "wiki" / "unexpected.md").write_text("unexpected")
        missing = status.build_fast_context_status(root=temp, scope="tenant-one")["knowledge"]["contextCoverage"]
        assert missing["missingSources"] == missing["unexpectedSources"] == 1
        wrong_scope = status.build_fast_context_status(root=temp, scope="tenant-two")["knowledge"]["contextCoverage"]
        assert wrong_scope["manifestStatus"] == "scopeMismatch"
        # Do not leak another scope's manifest inventory or an approval reference.
        assert wrong_scope["expectedSources"] is None
        assert "fixture-owner-approval" not in json.dumps(matched)
        for invalid in ({**manifest, "sources": manifest["sources"] * 2},
                        {**manifest, "approvalRef": ""},
                        {**manifest, "sources": [{**manifest["sources"][0], "path": "wiki/../secret.md"}]}):
            path.write_text(json.dumps(invalid))
            bad = status.build_fast_context_status(root=temp, scope="tenant-one")
            assert bad["knowledge"]["contextCoverage"]["manifestStatus"] == "invalid"
        (tenant / "raw").symlink_to(base / "wiki", target_is_directory=True)
        assert "raw/source.md" not in status._source_snapshot(tenant)

    # Fast status reads only allowed nonsecret settings, without changing env.
    (temp / ".env").write_text('MEMORY_SCOPE="tenant-default" # scope\nVAULT_PATH=elsewhere\nUNRELATED_TOKEN=secret\n')
    with mock.patch.dict(os.environ, {"PREPENDE_DOTENV_POLICY": "allow:MEMORY_SCOPE,VAULT_PATH",
                                    "VAULT_PATH": "ambient"}, clear=True):
        before = dict(os.environ)
        config = status._knowledge_settings(temp)
        assert config == {"MEMORY_SCOPE": "tenant-default", "VAULT_PATH": "ambient"}
        assert dict(os.environ) == before
        (temp / ".env").write_text("MEMORY_SCOPE=owner\nMEMORY_SCOPE=tenant-other\nVAULT_PATH=\nVAULT_PATH=first\nVAULT_PATH=second\n")
        os.environ.pop("VAULT_PATH")
        assert status._knowledge_settings(temp) == {"MEMORY_SCOPE": "owner", "VAULT_PATH": "first"}
        os.environ["PREPENDE_DOTENV_POLICY"] = "disabled"
        assert status._knowledge_settings(temp) == {}


def _online_failures_remain_unknown() -> None:
    with mock.patch.object(status, "_github_json", return_value=None):
        with (
            mock.patch.object(status, "_git", return_value="https://github.com/example/repository.git"),
        ):
            report = status._collect_online(
                True,
                {"authoritative": {}},
                {},
                Path("protocol"),
                Path("trust"),
                "pilot/status.json",
            )
    assert report["status"] == "unknown", report
    assert not any(report["checks"].values()), report


def _scope_isolation_and_secret_redaction(temp: Path) -> None:
    private = temp / "private"
    private.mkdir(parents=True)
    observed: list[str] = []

    def brain(_root: Path, scope: str, _python: Path) -> dict:
        observed.append(scope)
        return _ready_brain(scope)

    secret = "fixture-secret-value-that-must-not-appear"
    with (
        mock.patch.object(status, "_collect_brain", side_effect=brain),
        mock.patch.object(status, "_collect_recovery", return_value={"status": "ready", "proven": True}),
    ):
        payload, _code = _status_with_isolated_brain(
            root=private,
            scope="tenant-one",
            protocol_repo=None,
            trust_repo=None,
            online=False,
            environment={"UNRELATED_TOKEN": secret},
            python=Path(sys.executable),
        )
    encoded = json.dumps(payload, sort_keys=True)
    assert observed == ["tenant-one"], observed
    assert payload["brain"]["scope"] == "tenant-one", payload
    assert secret not in encoded and "UNRELATED_TOKEN" not in encoded, encoded
    forbidden = ("contract_id", "signature", "public_key", "private_key")
    assert not any(item in encoded.lower() for item in forbidden), encoded


def _invalid_arguments_return_two(temp: Path) -> None:
    with mock.patch.object(sys, "stderr"):
        assert status.main(["--not-a-real-option"], root=temp) == 2


def main() -> None:
    root = Path(tempfile.mkdtemp(prefix="prepende-operational-status-"))
    _unconfigured_never_discovers_siblings(root / "unconfigured")
    _configuration_precedence(root / "precedence")
    _wrong_repository_is_exit_two(root / "wrong-repo")
    _embedded_v02_is_never_authoritative(root / "embedded")
    _pilot_projection_is_authoritatively_compared(root / "pilot")
    _offline_brain_collection_is_byte_identical(root / "read-only")
    _scoped_corpus_coverage(root / "coverage")
    _online_failures_remain_unknown()
    _scope_isolation_and_secret_redaction(root / "redaction")
    _invalid_arguments_return_two(root / "args")
    print("PREPENDE OPERATIONAL STATUS SMOKE: OK")
    print("  explicit repositories only; CLI overrides environment")
    print("  Protocol v0.2 cannot satisfy authoritative v2 validation")
    print("  ledger verification outranks pilot projection")
    print("  offline collection is byte-for-byte read-only")
    print("  online failures remain unknown; tenant scope and secrets stay isolated")


if __name__ == "__main__":
    main()
