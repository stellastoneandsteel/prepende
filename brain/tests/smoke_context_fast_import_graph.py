#!/usr/bin/env python3
"""Smoke: the real context-fast path reads local truth without provider imports."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
FORBIDDEN_PREFIXES = (
    "agents",
    "connectors",
    "kernel.core.brain",
    "kernel.core.loop",
    "kernel.core.model_thought_bus",
    "kernel.core.semantic_meditation",
    "kernel.core.strategist",
    "kernel.core.verifier",
    "knowledge.rag",
    "knowledge.vault",
    "memory.factory",
    "memory.postgres_store",
    "models",
    "self_improve",
    "services.embedding_worker",
    "services.provider_service",
    "tactics",
    "anthropic",
    "asyncpg",
    "cohere",
    "google.generativeai",
    "httpx",
    "jax",
    "litellm",
    "llama_cpp",
    "openai",
    "replicate",
    "requests",
    "sentence_transformers",
    "tensorflow",
    "torch",
    "transformers",
    "urllib3",
    "vllm",
)
NETWORK_EVENTS = {
    "http.client.connect",
    "socket.connect",
    "socket.getaddrinfo",
    "urllib.Request",
}


def _matches_prefix(module_name: str, prefix: str) -> bool:
    return module_name == prefix or module_name.startswith(prefix + ".")


def _assert_embedding_profile_parity() -> None:
    """Pin the provider-free profile to the one the composition root persists.

    ``_configured_embedding_profile`` mirrors ``kernel.core.brain._embedding_profile``
    without building a gateway. A mirror that drifts silently reports a healthy
    index as semantically unready, so compare the two across every provider the
    factory accepts as an embedder, with no explicit model to fall back on.
    """
    from kernel.core.brain import _embedding_profile
    from models.factory import build_gateway
    from operations.local_status import (
        _EMBEDDING_DEFAULT_MODELS,
        _configured_embedding_profile,
    )

    for provider in sorted(_EMBEDDING_DEFAULT_MODELS):
        cfg = SimpleNamespace(
            provider="echo",
            embedding_provider=provider,
            embedding_model="",
            model="",
            embedding_dim=3,
            # Set to a NON-default value on purpose: the factory ignores
            # GROK_MODEL when it builds an embedding gateway, so a mirror that
            # honours it here would drift.
            grok_model="grok-4-fixture",
            anthropic_key="fixture",
            openai_key="fixture",
            google_key="fixture",
            xai_key="fixture",
            openai_compat_key="fixture",
            openai_compat_base="https://fixture.invalid/v1",
            local_base="http://fixture.invalid/v1",
        )
        gateway = build_gateway(cfg, provider=provider, model=None)
        expected = _embedding_profile(cfg, gateway)
        observed, reason = _configured_embedding_profile(cfg)
        assert reason is None, (provider, reason)
        assert observed == expected, {
            "provider": provider,
            "observed": observed,
            "canonical": expected,
        }


def _assert_connector_catalog_parity() -> None:
    sys.path.insert(0, str(ROOT))
    from connectors.defaults import BUILTIN_ADAPTERS
    from models.catalog import DEFAULT_MODEL_BY_PROVIDER
    from models.google import GoogleGateway
    from models.openai import OpenAIGateway
    from operations.local_status import _BUILTIN_TOOLS, _EMBEDDING_DEFAULT_MODELS

    expected = {}
    for adapter in BUILTIN_ADAPTERS:
        for tool in adapter.tools:
            capability = adapter.tool_capabilities[tool]
            expected[f"{adapter.name}.{tool}"] = {
                "connector": adapter.name,
                "configuredBy": adapter.auth_env or None,
                "supported": bool(capability["supported"]),
                "directCall": bool(capability["directCall"]),
            }
    observed = {
        str(tool["id"]): {
            "connector": str(tool["connector"]),
            "configuredBy": tool["configuredBy"],
            "supported": bool(tool["supported"]),
            "directCall": bool(tool["directCall"]),
        }
        for tool in _BUILTIN_TOOLS
    }
    assert observed == expected, {"observed": observed, "expected": expected}
    assert _EMBEDDING_DEFAULT_MODELS["openai"] == DEFAULT_MODEL_BY_PROVIDER["openai"]
    assert _EMBEDDING_DEFAULT_MODELS["anthropic"] == DEFAULT_MODEL_BY_PROVIDER["anthropic"]
    assert _EMBEDDING_DEFAULT_MODELS["google"] == GoogleGateway("", "").model
    assert _EMBEDDING_DEFAULT_MODELS["openai-compatible"] == OpenAIGateway(
        "", name="openai-compatible"
    ).model
    assert _EMBEDDING_DEFAULT_MODELS["local"] == OpenAIGateway(
        "", model="llama3", name="local"
    ).model
    assert _EMBEDDING_DEFAULT_MODELS["grok"] == OpenAIGateway(
        "", model="grok-2-latest", name="grok"
    ).model
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '  "operations",' in pyproject, "context-fast operations package omitted from wheel"


def _assert_collector_corruption_guards(
    fixture: Path, runtime: dict[str, Path], owner_scope: str
) -> None:
    from operations.local_status import (
        _configured_embedding_profile,
        _connectors_status,
        _embedding_dimension,
        _read_database,
    )

    for invalid in (
        "[NaN, 0, 0]",
        "[Infinity, 0, 0]",
        "[-Infinity, 0, 0]",
        "[true, 0, 0]",
        '["0.1", 0, 0]',
    ):
        assert _embedding_dimension(invalid) is None, invalid
    assert _embedding_dimension("[0.1, 0, 1]") == 3
    mixed_case_profile, mixed_case_reason = _configured_embedding_profile(
        SimpleNamespace(
            embedding_provider="OpenAI",
            embedding_model="",
            model="",
            grok_model="grok-2-latest",
            embedding_dim=3,
        )
    )
    assert mixed_case_profile == "OpenAI:gpt-5.6-sol:3:v1"
    assert mixed_case_reason is None

    race = fixture / "wal-race.db"
    with sqlite3.connect(race) as connection:
        connection.execute("CREATE TABLE items (value INTEGER NOT NULL)")
        connection.execute("INSERT INTO items VALUES (1)")
    writers: list[sqlite3.Connection] = []

    def race_reader(connection: sqlite3.Connection) -> dict[str, int]:
        writer = sqlite3.connect(race)
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("INSERT INTO items VALUES (2)")
        writer.commit()
        writers.append(writer)
        return {"count": int(connection.execute("SELECT COUNT(*) FROM items").fetchone()[0])}

    try:
        observed, reason = _read_database(race, race_reader)
        assert observed is None, observed
        assert reason == "database_has_uncheckpointed_wal", reason
    finally:
        for writer in writers:
            writer.close()

    cfg = SimpleNamespace(
        connector_readiness_db=str(runtime["readiness"]),
        workspace_scope=owner_scope,
    )
    with sqlite3.connect(runtime["readiness"]) as connection:
        connection.execute(
            "UPDATE connector_readiness_receipts SET evidence='[]' WHERE id='receipt-owner'"
        )
    malformed = _connectors_status(fixture, cfg, owner_scope)
    assert malformed["readiness_status"] == "unavailable", malformed
    assert malformed["ready"] == 0, malformed

    with sqlite3.connect(runtime["readiness"]) as connection:
        connection.execute(
            "UPDATE connector_readiness_receipts SET evidence=? WHERE id='receipt-owner'",
            (json.dumps({"operational": "false"}),),
        )
    wrong_type = _connectors_status(fixture, cfg, owner_scope)
    assert wrong_type["readiness_status"] == "observed", wrong_type
    assert wrong_type["ready"] == 0, wrong_type

    with sqlite3.connect(runtime["readiness"]) as connection:
        connection.execute(
            "UPDATE connector_readiness_receipts SET evidence=? WHERE id='receipt-owner'",
            (json.dumps({"operational": True}),),
        )


def _tree_snapshot(root: Path) -> dict[str, tuple[int, int, int, str]]:
    snapshot: dict[str, tuple[int, int, int, str]] = {}
    if not root.exists():
        return snapshot
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(part in {".git", ".venv"} for part in relative.parts):
            continue
        if not path.is_file():
            continue
        info = path.stat()
        snapshot[relative.as_posix()] = (
            stat.S_IMODE(info.st_mode),
            int(info.st_mtime_ns),
            int(info.st_size),
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
    return snapshot


def _create_indexed_page(vault: Path, index: Path, title: str, body: str) -> None:
    wiki = vault / "wiki"
    wiki.mkdir(parents=True)
    index.parent.mkdir(parents=True, exist_ok=True)
    page = wiki / f"{title}.md"
    page.write_text(f"# {title.title()}\n\n{body}\n", encoding="utf-8")
    page_info = page.stat()
    page_bytes = page.read_bytes()

    with sqlite3.connect(index) as connection:
        connection.execute(
            "CREATE TABLE source_files ("
            "path TEXT PRIMARY KEY,mtime_ns INTEGER NOT NULL,size INTEGER NOT NULL,"
            "content_hash TEXT NOT NULL,chunk_count INTEGER NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE chunks ("
            "id TEXT PRIMARY KEY,path TEXT NOT NULL,page TEXT NOT NULL,section TEXT,"
            "content TEXT NOT NULL,mtime REAL NOT NULL,embedding TEXT,"
            "metadata TEXT NOT NULL DEFAULT '{}')"
        )
        connection.execute(
            "CREATE TABLE index_meta (key TEXT PRIMARY KEY,value TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO source_files VALUES (?,?,?,?,?)",
            (
                f"wiki/{title}.md",
                int(page_info.st_mtime_ns),
                len(page_bytes),
                hashlib.sha256(page_bytes).hexdigest(),
                1,
            ),
        )
        connection.execute(
            "INSERT INTO chunks VALUES (?,?,?,?,?,?,?,?)",
            (
                f"chunk-{title}",
                f"wiki/{title}.md",
                title,
                title.title(),
                body,
                float(page_info.st_mtime),
                json.dumps([0.1, 0.2, 0.3]),
                "{}",
            ),
        )
        connection.executemany(
            "INSERT INTO index_meta VALUES (?,?)",
            [
                ("embedding_profile", "openai:gpt-5.6-sol:3:v1"),
                ("embedding_dimension", "3"),
            ],
        )


def _create_runtime_fixture(
    root: Path, owner_scope: str, tenant_scope: str
) -> dict[str, Path]:
    state = root / "state"
    vault = root / "vault"
    state.mkdir()
    owner_index = state / "vault-index.db"
    _create_indexed_page(
        vault,
        owner_index,
        "owner-continuity",
        "Verified owner-only local context.",
    )
    tenant_vault = vault / "tenants" / tenant_scope
    tenant_digest = hashlib.sha256(str(tenant_vault).encode("utf-8")).hexdigest()[:16]
    tenant_index = state / "vault_indexes" / f"{tenant_digest}.db"
    _create_indexed_page(
        tenant_vault,
        tenant_index,
        "tenant-continuity",
        "Verified tenant-only local context.",
    )

    memory = state / "memory.db"
    with sqlite3.connect(memory) as connection:
        connection.execute(
            "CREATE TABLE memories ("
            "id TEXT PRIMARY KEY,scope TEXT NOT NULL,content TEXT NOT NULL,"
            "metadata TEXT NOT NULL DEFAULT '{}',created_at REAL NOT NULL,"
            "status TEXT NOT NULL DEFAULT 'active',superseded_by TEXT)"
        )
        now = time.time()
        connection.executemany(
            "INSERT INTO memories VALUES (?,?,?,?,?,?,?)",
            [
                (
                    "memory-owner",
                    owner_scope,
                    "Real owner local memory",
                    "{}",
                    now,
                    "active",
                    None,
                ),
                (
                    "memory-tenant",
                    tenant_scope,
                    "Real tenant local memory",
                    "{}",
                    now + 1,
                    "active",
                    None,
                ),
            ],
        )

    runs = state / "runs.db"
    with sqlite3.connect(runs) as connection:
        connection.execute(
            "CREATE TABLE runs ("
            "goal_id TEXT PRIMARY KEY,goal TEXT NOT NULL,status TEXT NOT NULL,"
            "result TEXT,error TEXT,started REAL NOT NULL,updated REAL NOT NULL)"
        )
        now = time.time()
        connection.execute(
            "INSERT INTO runs VALUES (?,?,?,?,?,?,?)",
            ("run-1", "Real local goal", "done", "ok", None, now, now),
        )

    readiness = state / "connector-readiness.db"
    with sqlite3.connect(readiness) as connection:
        connection.execute(
            "CREATE TABLE connector_readiness_receipts ("
            "id TEXT PRIMARY KEY,tenant_id TEXT NOT NULL,workspace_id TEXT NOT NULL,"
            "connector TEXT NOT NULL,connector_version TEXT NOT NULL,"
            "probe_type TEXT NOT NULL,status TEXT NOT NULL,reason TEXT NOT NULL DEFAULT '',"
            "evidence TEXT NOT NULL DEFAULT '{}',created_at REAL NOT NULL,"
            "expires_at REAL NOT NULL)"
        )
        now = time.time()
        connection.executemany(
            "INSERT INTO connector_readiness_receipts VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    "receipt-owner",
                    owner_scope,
                    owner_scope,
                    "news",
                    "1",
                    "rss_fetch_read_only",
                    "verified",
                    "",
                    json.dumps({"operational": True}),
                    now,
                    now + 300,
                ),
                (
                    "receipt-tenant",
                    tenant_scope,
                    owner_scope,
                    "news",
                    "1",
                    "rss_fetch_read_only",
                    "verified",
                    "",
                    json.dumps({"operational": True}),
                    now,
                    now + 300,
                ),
            ],
        )

    return {
        "state": state,
        "vault": vault,
        "tenant_vault": tenant_vault,
        "index": owner_index,
        "tenant_index": tenant_index,
        "memory": memory,
        "runs": runs,
        "readiness": readiness,
    }


def _write_audit_hook(directory: Path, traces: Path) -> None:
    directory.mkdir()
    traces.mkdir()
    (directory / "sitecustomize.py").write_text(
        """
import atexit
import json
import os
import sys
from pathlib import Path

_events = []
_trace_dir = Path(os.environ["PREPENDE_CONTEXT_FAST_AUDIT_DIR"])


def _audit(event, args):
    if event == "import" and args and isinstance(args[0], str):
        _events.append({"event": "import", "name": args[0]})
    elif event in {"http.client.connect", "socket.connect", "socket.getaddrinfo", "urllib.Request"}:
        _events.append({"event": event})
    elif event == "subprocess.Popen":
        raw_argv = args[1] if len(args) > 1 else []
        argv = [str(item) for item in raw_argv] if isinstance(raw_argv, (list, tuple)) else [str(raw_argv)]
        _events.append({"event": event, "executable": str(args[0]), "argv": argv})


sys.addaudithook(_audit)


@atexit.register
def _flush():
    target = _trace_dir / (str(os.getpid()) + ".json")
    target.write_text(json.dumps(_events, sort_keys=True), encoding="utf-8")
""".lstrip(),
        encoding="utf-8",
    )


def _audit_events(traces: Path) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for path in sorted(traces.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(payload, list), payload
        events.extend(item for item in payload if isinstance(item, dict))
    return events


def _git_index_snapshot() -> tuple[int, int, int, str]:
    env = os.environ.copy()
    env["GIT_OPTIONAL_LOCKS"] = "0"
    proc = subprocess.run(
        ["git", "rev-parse", "--git-path", "index"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    path = Path(proc.stdout.strip())
    if not path.is_absolute():
        path = (ROOT / path).resolve()
    info = path.stat()
    return (
        stat.S_IMODE(info.st_mode),
        int(info.st_mtime_ns),
        int(info.st_size),
        hashlib.sha256(path.read_bytes()).hexdigest(),
    )


def _run_context_fast(env: dict[str, str], scope: str) -> dict[str, object]:
    process = subprocess.run(
        [
            sys.executable,
            str(ROOT / "bin" / "engram"),
            "context-fast",
            f"Verify real provider-free continuity for {scope}",
            "--json",
            "--scope",
            scope,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=env,
        timeout=20,
    )
    assert process.returncode == 0, process.stderr or process.stdout
    assert "fixture-key-must-not-be-used" not in process.stdout
    return json.loads(process.stdout)


def _assert_common_payload(payload: dict[str, object], scope: str) -> dict[str, object]:
    """Assert what `bin/engram context-fast` actually returns.

    The fast lane's status comes from
    ``operations.operational_status.build_fast_context_status``, which is the
    deliberately narrow, model-free subset: it never composes the kernel, so it
    reports ``model: "skipped"`` rather than the configured provider, and it
    carries no connector inventory (``_collect_brain`` fixes connectors at
    ``{"status": "notConfigured", "available": 0, "ready": 0, "reason":
    "offline_probe_disabled"}``), no scoped memory, and no run history.

    The richer, scope-isolated truth those fields would carry is asserted
    against the collector itself in ``_assert_scoped_collector_truth`` -- see the
    note there for why it is checked at the collector rather than through this
    payload.
    """

    assert payload["command"] == "context-fast", payload
    assert payload["modelCall"] == "skipped", payload
    assert payload["verdict"]["continuityReady"] is True, payload["continuity"]
    status_payload = payload["status"]
    assert status_payload["scope"] == scope, status_payload
    assert status_payload["model"] == "skipped", status_payload
    # The same guarantee `model_status: {"initialized": false}` states, in the
    # shape this payload uses. Both say: no model was constructed, none called.
    assert status_payload["fastLane"] == {
        "modelCall": False,
        "liveProviderCall": False,
    }, status_payload
    rag = status_payload["knowledge"]["rag"]
    assert status_payload["knowledge"]["pages"] == 1, status_payload
    assert rag["source_files"] == 1 and rag["indexed_files"] == 1, rag
    assert rag["chunks"] == 1 and rag["lexical_ready"] is True, rag
    assert rag["stale"] is False, rag
    # Not a count. The offline probe is disabled on this lane, so zero here means
    # "not inspected", and asserting any other number would assert a feature the
    # fast lane does not have.
    assert status_payload["connectors"] == {"tools": 0, "ready": 0}, status_payload
    return status_payload


def _assert_scoped_collector_truth(
    root: Path,
    env: dict[str, str],
    owner_scope: str,
    tenant_scope: str,
) -> None:
    """Assert scope isolation where it is actually implemented.

    ``operations.local_status.collect_context_fast_status`` is the provider-free
    collector that does report the configured provider, real connector
    readiness, scoped memory and scoped run history. ``kernel --status
    --context-fast`` uses it; the ``bin/engram`` fast lane does not.

    These properties -- a tenant never seeing owner knowledge, graphify refusing
    a tenant scope, the run journal declaring itself unpartitioned -- are
    properties of the collector, so they are asserted against the collector. In
    process, so this adds nothing for the process allowlist below to account for.
    """

    from operations.local_status import collect_context_fast_status

    def collect(scope: str, overrides: dict[str, str] | None = None) -> dict[str, object]:
        values = dict(env)
        values.update(overrides or {})
        with mock.patch.dict(os.environ, values, clear=True):
            return collect_context_fast_status(root, scope)

    owner = collect(owner_scope)
    assert owner["model"] == "anthropic", owner
    assert owner["model_status"]["initialized"] is False, owner
    assert owner["memory"]["recent"] == ["Real owner local memory"], owner["memory"]
    assert owner["knowledge"]["titles"] == ["owner-continuity"], owner["knowledge"]
    assert owner["runs"]["recent_count"] == 1, owner["runs"]
    assert owner["runs"]["recent"][0]["goal"] == "Real local goal", owner["runs"]
    connectors = owner["connectors"]
    assert connectors["tools"] == 4, connectors
    assert connectors["ready"] == 1, connectors
    assert connectors["ready_ids"] == ["news.fetch_headlines"], connectors
    assert connectors["dynamic_mcp_status"] == "uninspected", connectors

    # MEMORY_BACKEND=auto must not probe the unreachable Postgres it is handed.
    auto = collect(owner_scope, {
        "MEMORY_BACKEND": "auto",
        "DATABASE_URL": "postgresql://127.0.0.1:9/fixture-must-not-connect",
    })
    auto_memory = auto["memory"]
    assert auto_memory["backend"] == "auto", auto_memory
    assert auto_memory["status"] == "selection_uninspected", auto_memory
    assert auto_memory["recent"] == ["Real owner local memory"], auto_memory
    assert auto_memory["local_fallback"]["backend"] == "sqlite", auto_memory
    assert auto_memory["local_fallback"]["recent_count"] == 1, auto_memory

    # With no explicit embedding model the collector must still resolve the same
    # profile the composition root persists, or a healthy index reads as unready.
    default_rag = collect(owner_scope, {"EMBEDDING_MODEL": "", "MODEL_NAME": ""})["knowledge"]["rag"]
    assert default_rag["configured_profile"] == "openai:gpt-5.6-sol:3:v1", default_rag
    assert default_rag["semantic_ready"] is True, default_rag

    tenant = collect(tenant_scope)
    assert tenant["memory"]["recent"] == ["Real tenant local memory"], tenant["memory"]
    assert tenant["knowledge"]["titles"] == ["tenant-continuity"], tenant["knowledge"]
    assert "owner" not in json.dumps(tenant["knowledge"]).lower(), tenant["knowledge"]
    assert tenant["knowledge"]["graphify"]["reason"] == (
        "not_configured_for_tenant_scope"
    ), tenant["knowledge"]
    assert tenant["runs"]["recent_count"] is None, tenant["runs"]
    assert tenant["runs"]["reason"] == "run_journal_not_scope_partitioned", tenant["runs"]


def _open_live_wal(path: Path) -> sqlite3.Connection:
    """Hold an open WAL connection, exactly as a running brain does."""
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.commit()
    return connection


def _assert_live_wal_status_is_real(root: Path, base_env: dict[str, str], scope: str) -> None:
    """A running brain must not read as an unavailable one.

    The runtime opens every store it writes with `journal_mode=WAL`, and SQLite
    keeps the `-wal` file until the last connection closes. Reading such a
    database with `immutable=1` skips the WAL, so the collector has to take
    SQLite's locked read-only path instead -- and must still not write.
    """
    fixture = root / "live-wal"
    fixture.mkdir()
    runtime = _create_runtime_fixture(fixture, scope, "tenant-alpha")

    live = [
        _open_live_wal(runtime["memory"]),
        _open_live_wal(runtime["runs"]),
        _open_live_wal(runtime["readiness"]),
        _open_live_wal(runtime["index"]),
    ]
    try:
        # Commit through the live connections so the rows under test exist only
        # in the WAL, which is precisely what `immutable=1` cannot see.
        live[0].execute(
            "INSERT INTO memories VALUES (?,?,?,?,?,?,?)",
            ("memory-wal", scope, "Committed in the WAL", "{}", time.time() + 10, "active", None),
        )
        live[0].commit()

        payloads = {
            path: path.read_bytes()
            for path in sorted(runtime["state"].rglob("*.db"))
        }
        wal_payloads = {
            path: path.read_bytes()
            for path in sorted(runtime["state"].rglob("*.db-wal"))
        }
        assert wal_payloads, "fixture did not produce a live -wal file"

        env = base_env.copy()
        env.update(
            {
                "MEMORY_DB": str(runtime["memory"]),
                "RUNS_DB": str(runtime["runs"]),
                "CONNECTOR_READINESS_DB": str(runtime["readiness"]),
                "VAULT_PATH": str(runtime["vault"]),
                "VAULT_INDEX_PATH": str(runtime["index"]),
                "GRAPHIFY_GRAPH_PATH": str(fixture / "missing-graph.json"),
            }
        )
        for key in ("PYTHONPATH", "PREPENDE_CONTEXT_FAST_AUDIT_DIR"):
            env.pop(key, None)

        # Through the CLI, for the knowledge index the fast lane does read, and
        # for the byte-immutability check below.
        status = _run_context_fast(env, scope)["status"]
        assert status["knowledge"]["rag"]["lexical_ready"] is True, status["knowledge"]

        # Memory, runs and connector readiness live in the scoped collector, not
        # in the fast-lane subset -- same split as _assert_scoped_collector_truth.
        from operations.local_status import collect_context_fast_status

        with mock.patch.dict(os.environ, env, clear=True):
            collected = collect_context_fast_status(fixture, scope)
        memory = collected["memory"]
        assert memory["status"] == "ready", memory
        assert memory["recent"][0] == "Committed in the WAL", memory
        assert collected["runs"]["recent_count"] == 1, collected["runs"]
        assert collected["connectors"]["readiness_status"] == "observed", collected["connectors"]

        # The locked read may map the `-shm` wal-index the writer already owns.
        # It must never alter database or WAL bytes.
        assert {path: path.read_bytes() for path in payloads} == payloads
        assert {path: path.read_bytes() for path in wal_payloads} == wal_payloads
    finally:
        for connection in live:
            connection.close()


def _assert_process_allowlist(events: list[dict[str, object]], audited_runs: int) -> None:
    launches = [item for item in events if item.get("event") == "subprocess.Popen"]
    kernel_launches = 0
    git_launches: list[tuple[str, ...]] = []
    allowed_git = {
        ("rev-parse", "--show-toplevel"),
        ("branch", "--show-current"),
        ("rev-parse", "HEAD"),
        ("status", "--porcelain=v1"),
        ("rev-parse", "--abbrev-ref", "@{upstream}"),
        ("remote",),
    }
    # repository_snapshot resolves the upstream commit only when the branch has
    # an upstream (operations/continuity.py:104), so this one is present in every
    # run or absent from every run. A detached CI checkout has no upstream.
    conditional_git = {
        ("rev-parse", "@{upstream}"),
    }
    for item in launches:
        executable = str(item.get("executable") or "")
        argv = tuple(str(value) for value in item.get("argv", []))
        if Path(executable).name == "git":
            assert len(argv) >= 4 and argv[:3] == ("git", "-C", str(ROOT)), item
            command = argv[3:]
            assert command in allowed_git | conditional_git, item
            git_launches.append(command)
            continue
        if Path(executable).name.startswith("python"):
            assert argv[:3] == (str(ROOT / ".venv" / "bin" / "python3"), "-m", "kernel"), item
            assert "--status" in argv and "--context-fast" in argv, item
            kernel_launches += 1
            continue
        raise AssertionError(f"unreviewed process launch: {item}")
    # Derived from the audited runs rather than hardcoded, so adding a scenario
    # does not fail on an opaque count.
    # The fast lane collects in-process, so it must not spawn the kernel at all.
    # The branch above still fails an unreviewed python launch; this pins the
    # count at zero so reintroducing a subprocess is a deliberate, visible change.
    assert kernel_launches == 0, launches
    assert audited_runs > 0
    observed_git = set(git_launches)
    assert allowed_git <= observed_git, sorted(allowed_git - observed_git)
    # Every run makes the same set of git calls exactly once, whichever set that
    # is. This still fails on a repeated or dropped call; it only tolerates the
    # upstream lookup being uniformly present or uniformly absent.
    assert len(git_launches) == audited_runs * len(observed_git), launches


def main() -> None:
    _assert_connector_catalog_parity()
    _assert_embedding_profile_parity()
    owner_scope = "prepende"
    tenant_scope = "tenant-alpha"
    with tempfile.TemporaryDirectory(prefix="prepende-context-fast-real-") as directory:
        fixture = Path(directory).resolve()
        runtime = _create_runtime_fixture(fixture, owner_scope, tenant_scope)
        _assert_collector_corruption_guards(fixture, runtime, owner_scope)
        audit_path = fixture / "audit"
        traces = fixture / "traces"
        _write_audit_hook(audit_path, traces)

        repository_before = _tree_snapshot(ROOT)
        runtime_before = _tree_snapshot(runtime["state"])
        vault_before = _tree_snapshot(runtime["vault"])
        index_before = _git_index_snapshot()

        env = os.environ.copy()
        env.update(
            {
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONNOUSERSITE": "1",
                "PYTHONPATH": str(audit_path),
                "PREPENDE_CONTEXT_FAST_AUDIT_DIR": str(traces),
                "MODEL_PROVIDER": "anthropic",
                "MODEL_NAME": "provider-must-not-load",
                "ANTHROPIC_API_KEY": "fixture-key-must-not-be-used",
                "EMBEDDING_PROVIDER": "openai",
                "EMBEDDING_MODEL": "embedding-must-not-load",
                "EMBEDDING_DIM": "3",
                "OPENAI_API_KEY": "fixture-key-must-not-be-used",
                "MEMORY_BACKEND": "sqlite",
                "DATABASE_URL": "",
                "MEMORY_SCOPE": owner_scope,
                "WORKSPACE_SCOPE": owner_scope,
                "MEMORY_DB": str(runtime["memory"]),
                "RUNS_DB": str(runtime["runs"]),
                "CONNECTOR_READINESS_DB": str(runtime["readiness"]),
                "VAULT_PATH": str(runtime["vault"]),
                "VAULT_INDEX_PATH": str(runtime["index"]),
                "GRAPHIFY_GRAPH_PATH": str(fixture / "missing-graph.json"),
                "PREPENDE_MCP_SERVERS": json.dumps(
                    [{"name": "must-not-connect", "url": "http://127.0.0.1:9/mcp"}]
                ),
            }
        )
        # Four CLI runs under four environments. Each one is audited, so the
        # import and network assertions below cover the fast lane as configured
        # for a real owner, a Postgres-configured owner, a defaulted embedding
        # profile, and a tenant -- not just one happy path.
        owner_payload = _run_context_fast(env, owner_scope)
        _assert_common_payload(owner_payload, owner_scope)

        auto_env = env.copy()
        auto_env.update(
            {
                "MEMORY_BACKEND": "auto",
                "DATABASE_URL": "postgresql://127.0.0.1:9/fixture-must-not-connect",
            }
        )
        _assert_common_payload(_run_context_fast(auto_env, owner_scope), owner_scope)

        default_embedding_env = env.copy()
        default_embedding_env.update({"EMBEDDING_MODEL": "", "MODEL_NAME": ""})
        _assert_common_payload(
            _run_context_fast(default_embedding_env, owner_scope), owner_scope
        )

        _assert_common_payload(_run_context_fast(env, tenant_scope), tenant_scope)

        _assert_scoped_collector_truth(fixture, env, owner_scope, tenant_scope)

        assert _tree_snapshot(runtime["state"]) == runtime_before
        assert _tree_snapshot(runtime["vault"]) == vault_before
        assert _tree_snapshot(ROOT) == repository_before
        assert _git_index_snapshot() == index_before

        events = _audit_events(traces)
        # One trace per audited run. context-fast collects in-process via
        # operations.operational_status.build_fast_context_status, so the wrapper
        # is the only process doing the work and the only one to audit. The
        # forbidden-import and network assertions below therefore cover all of
        # it; there is no child process for a provider import to hide in.
        assert len(list(traces.glob("*.json"))) == 4, sorted(
            path.name for path in traces.glob("*.json")
        )
        imports = {
            str(item.get("name"))
            for item in events
            if item.get("event") == "import" and item.get("name")
        }
        forbidden = sorted(
            module
            for module in imports
            if any(_matches_prefix(module, prefix) for prefix in FORBIDDEN_PREFIXES)
        )
        network = sorted(
            str(item["event"])
            for item in events
            if item.get("event") in NETWORK_EVENTS
        )
        assert forbidden == [], f"provider/composition imports on context-fast path: {forbidden}"
        assert network == [], f"network activity on context-fast path: {network}"
        _assert_process_allowlist(events, audited_runs=4)

        # Runs outside the audit hook, so it neither perturbs the process
        # allowlist above nor relaxes the no-write snapshots taken there.
        _assert_live_wal_status_is_real(fixture, env, owner_scope)

    print("smoke_context_fast_import_graph OK")


if __name__ == "__main__":
    main()
