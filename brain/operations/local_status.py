"""Provider-free, read-only local status for the context-fast continuity lane.

This module deliberately inspects existing files and SQLite databases directly.
It never constructs the brain composition root, initializes a provider, creates
runtime state, discovers live MCP tools, or opens a network connection.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import stat
import time
from pathlib import Path
from typing import Any, Callable


_SCOPE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_POSTGRES_SCHEMES = ("postgres://", "postgresql://")
_EMBEDDING_DEFAULT_MODELS = {
    "anthropic": "claude-fable-5",
    "cli-claude": "default",
    "claude-cli": "default",
    "cli-codex": "default",
    "codex-cli": "default",
    "echo": "default",
    "google": "gemini-2.0-flash",
    "grok": "grok-2-latest",
    "local": "llama3",
    "openai": "gpt-5.6-sol",
    "openai-compatible": "gpt-5.6-sol",
    "xai": "grok-2-latest",
}
_BUILTIN_TOOLS = (
    {
        "id": "n8n.run_workflow",
        "connector": "n8n",
        "supported": True,
        "directCall": False,
        "configuredBy": "N8N_WEBHOOK_URL",
    },
    {
        "id": "figma.get_design",
        "connector": "figma",
        "supported": True,
        "directCall": True,
        "configuredBy": "FIGMA_API_KEY",
    },
    {
        "id": "figma.create_design",
        "connector": "figma",
        "supported": False,
        "directCall": False,
        "configuredBy": "FIGMA_API_KEY",
    },
    {
        "id": "news.fetch_headlines",
        "connector": "news",
        "supported": True,
        "directCall": True,
        "configuredBy": None,
    },
)


def _resolve(root: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return path.resolve(strict=False) if path.is_absolute() else (root / path).resolve(strict=False)


def _file_signature(path: Path) -> tuple[int, int, int, int, int] | None:
    try:
        info = path.stat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode):
        raise OSError("status input is not a regular file")
    return (
        int(info.st_dev),
        int(info.st_ino),
        int(info.st_size),
        int(info.st_mtime_ns),
        int(info.st_ctime_ns),
    )


def _database_signature(
    path: Path,
) -> tuple[tuple[int, int, int, int, int] | None, tuple[int, int, int, int, int] | None]:
    return _file_signature(path), _file_signature(Path(f"{path}-wal"))


def _read_database(
    path: Path,
    reader: Callable[[sqlite3.Connection], dict[str, Any]],
) -> tuple[dict[str, Any] | None, str | None]:
    try:
        before = _database_signature(path)
    except OSError:
        return None, "database_unreadable"
    if before[0] is None:
        return None, "database_missing"
    if before[1] is not None and before[1][2] > 0:
        return None, "database_has_uncheckpointed_wal"

    try:
        connection = sqlite3.connect(
            f"file:{path.as_posix()}?mode=ro&immutable=1",
            uri=True,
            timeout=1.0,
        )
        connection.row_factory = sqlite3.Row
        try:
            observed = reader(connection)
        finally:
            connection.close()
    except (OSError, sqlite3.Error, ValueError, TypeError, json.JSONDecodeError):
        return None, "database_unreadable"

    try:
        after = _database_signature(path)
    except OSError:
        return None, "database_unreadable"
    if after[1] is not None and after[1][2] > 0:
        return None, "database_has_uncheckpointed_wal"
    if after != before:
        return None, "database_changed_during_read"
    return observed, None


def _embedding_dimension(raw: Any) -> int | None:
    try:
        vector = json.loads(str(raw))
        if (
            not isinstance(vector, list)
            or not vector
            or any(
                isinstance(item, bool)
                or not isinstance(item, (int, float))
                or not math.isfinite(item)
                for item in vector
            )
        ):
            return None
    except (OverflowError, TypeError, ValueError, json.JSONDecodeError):
        return None
    return len(vector)


def _configured_embedding_profile(cfg: Any) -> tuple[str, str | None]:
    provider = str(cfg.embedding_provider or "").strip()
    provider_key = provider.lower()
    if not provider_key:
        return "", None
    model = str(cfg.embedding_model or cfg.model or "").strip()
    if not model and provider_key in {"grok", "xai"}:
        model = str(cfg.grok_model or "grok-2-latest").strip()
    if not model:
        model = _EMBEDDING_DEFAULT_MODELS.get(provider_key, "")
    if not model:
        return "", "configured_embedding_model_unresolved"
    return f"{provider}:{model}:{cfg.embedding_dim}:v1", None


def _sqlite_memory_status(root: Path, cfg: Any, scope: str) -> dict[str, Any]:
    path = _resolve(root, cfg.memory_db)

    def read(connection: sqlite3.Connection) -> dict[str, Any]:
        columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(memories)").fetchall()
        }
        if not {"scope", "content", "created_at"}.issubset(columns):
            raise sqlite3.DatabaseError("memory schema unavailable")
        filters = ["scope=?"]
        if "status" in columns:
            filters.append("status != 'deleted'")
        if "superseded_by" in columns:
            filters.append("superseded_by IS NULL")
        rows = connection.execute(
            "SELECT content FROM memories WHERE "
            + " AND ".join(filters)
            + " ORDER BY created_at DESC LIMIT 5",
            (scope,),
        ).fetchall()
        recent = [str(row["content"])[:120] for row in rows]
        return {
            "backend": "sqlite",
            "status": "ready",
            "recent_count": len(recent),
            "recent": recent,
        }

    observed, reason = _read_database(path, read)
    if observed is not None:
        return observed
    return {
        "backend": "sqlite",
        "status": "unavailable",
        "reason": reason,
        "recent_count": None,
        "recent": [],
    }


def _memory_status(root: Path, cfg: Any, scope: str) -> dict[str, Any]:
    postgres_configured = str(cfg.database_url).startswith(_POSTGRES_SCHEMES)
    if cfg.memory_backend == "postgres":
        return {
            "backend": "postgres",
            "status": "uninspected",
            "reason": "remote_memory_backend_not_inspected",
            "recent_count": None,
            "recent": [],
        }

    local = _sqlite_memory_status(root, cfg, scope)
    if cfg.memory_backend == "auto" and postgres_configured:
        # The normal composition root may choose Postgres only after importing
        # asyncpg and probing the configured host. Context-fast does neither,
        # so preserve the real local fallback without pretending which backend
        # a separate live run would select.
        return {
            "backend": "auto",
            "status": "selection_uninspected",
            "reason": "auto_postgres_selection_not_probed",
            "recent_count": local.get("recent_count"),
            "recent": local.get("recent", []),
            "local_fallback": local,
            "remote": {
                "status": "uninspected",
                "reason": "live_network_probe_skipped",
            },
        }
    return local


def _runs_status(root: Path, cfg: Any, scope: str) -> dict[str, Any]:
    if scope != cfg.memory_scope:
        return {
            "status": "unavailable",
            "reason": "run_journal_not_scope_partitioned",
            "recent_count": None,
            "recent": [],
        }
    path = _resolve(root, cfg.runs_db)

    def read(connection: sqlite3.Connection) -> dict[str, Any]:
        columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(runs)").fetchall()
        }
        if not {"goal", "status", "updated"}.issubset(columns):
            raise sqlite3.DatabaseError("run schema unavailable")
        rows = connection.execute(
            "SELECT goal,status FROM runs ORDER BY updated DESC LIMIT 5"
        ).fetchall()
        recent = [
            {"goal": str(row["goal"])[:80], "status": str(row["status"])}
            for row in rows
        ]
        return {"status": "ready", "recent_count": len(recent), "recent": recent}

    observed, reason = _read_database(path, read)
    if observed is not None:
        return observed
    return {
        "status": "unavailable",
        "reason": reason,
        "recent_count": None,
        "recent": [],
    }


def _source_snapshot(vault: Path) -> dict[str, tuple[int, int, str]]:
    result: dict[str, tuple[int, int, str]] = {}
    for directory in ("wiki", "raw"):
        base = vault / directory
        if not base.is_dir():
            continue
        for path in sorted(base.glob("*.md")):
            if not path.is_file() or path.is_symlink():
                continue
            before = path.stat()
            first = path.read_bytes()
            middle = path.stat()
            payload = path.read_bytes()
            after = path.stat()
            before_signature = (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
            )
            middle_signature = (
                middle.st_dev,
                middle.st_ino,
                middle.st_size,
                middle.st_mtime_ns,
                middle.st_ctime_ns,
            )
            after_signature = (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            )
            if (
                before_signature != middle_signature
                or middle_signature != after_signature
                or first != payload
                or len(payload) != after.st_size
            ):
                raise OSError("knowledge source changed during status read")
            result[path.relative_to(vault).as_posix()] = (
                int(after.st_mtime_ns),
                len(payload),
                hashlib.sha256(payload).hexdigest(),
            )
    return result


def _index_path(root: Path, cfg: Any, vault: Path) -> Path:
    memory_db = _resolve(root, cfg.memory_db)
    owner_vault = _resolve(root, cfg.vault)
    if vault == owner_vault:
        override = (os.environ.get("VAULT_INDEX_PATH") or "").strip()
        return _resolve(root, override) if override else memory_db.parent / "vault_index.db"
    digest = hashlib.sha256(str(vault).encode("utf-8")).hexdigest()[:16]
    return memory_db.parent / "vault_indexes" / f"{digest}.db"


def _read_rag_index(
    path: Path,
    sources: dict[str, tuple[int, int, str]],
    cfg: Any,
) -> dict[str, Any]:
    configured_profile, profile_reason = _configured_embedding_profile(cfg)
    configured_dimension = cfg.embedding_dim if configured_profile else None

    def read(connection: sqlite3.Connection) -> dict[str, Any]:
        tables = {
            str(row["name"])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if not {"source_files", "chunks"}.issubset(tables):
            raise sqlite3.DatabaseError("RAG schema unavailable")
        indexed = {
            str(row["path"]): (
                int(row["mtime_ns"]), int(row["size"]), str(row["content_hash"])
            )
            for row in connection.execute(
                "SELECT path,mtime_ns,size,content_hash FROM source_files"
            ).fetchall()
        }
        rows = connection.execute("SELECT path,embedding FROM chunks").fetchall()
        persisted: dict[str, str] = {}
        if "index_meta" in tables:
            persisted = {
                str(row["key"]): str(row["value"])
                for row in connection.execute("SELECT key,value FROM index_meta").fetchall()
            }
        return {"indexed": indexed, "rows": rows, "persisted": persisted}

    observed, reason = _read_database(path, read)
    if observed is None:
        return {
            "source_files": len(sources),
            "indexed_files": None,
            "chunks": None,
            "embedded_chunks": None,
            "missing_embeddings": None,
            "configured_profile": configured_profile,
            "profile_reason": profile_reason,
            "persisted_profile": None,
            "actual_dimension": None,
            "lexical_ready": False,
            "semantic_ready": False,
            "stale": True,
            "reason": reason,
        }

    indexed = observed["indexed"]
    rows = observed["rows"]
    persisted = observed["persisted"]
    chunks = len(rows)
    chunk_paths = {str(row["path"]) for row in rows}
    persisted_profile = persisted.get("embedding_profile", "")
    try:
        persisted_dimension = int(persisted["embedding_dimension"])
        if persisted_dimension <= 0:
            persisted_dimension = None
    except (KeyError, TypeError, ValueError):
        persisted_dimension = None

    expected_dimension = configured_dimension or persisted_dimension
    dimensions: set[int] = set()
    valid_embedded = 0
    invalid_embedded = 0
    for row in rows:
        raw = row["embedding"]
        if raw is None:
            continue
        dimension = _embedding_dimension(raw)
        if dimension is None:
            invalid_embedded += 1
            continue
        dimensions.add(dimension)
        if expected_dimension is not None and dimension == expected_dimension:
            valid_embedded += 1
        else:
            invalid_embedded += 1

    stale = (
        sources != indexed
        or bool(chunk_paths.difference(indexed))
    )
    profile_matches = configured_profile == persisted_profile
    dimension_ready = (
        expected_dimension is not None
        and persisted_dimension == expected_dimension
        and invalid_embedded == 0
        and (not dimensions or dimensions == {expected_dimension})
    )
    actual_dimension = next(iter(dimensions)) if len(dimensions) == 1 else None
    return {
        "source_files": len(sources),
        "indexed_files": len(indexed),
        "chunks": chunks,
        "embedded_chunks": valid_embedded,
        "missing_embeddings": max(0, chunks - valid_embedded),
        "configured_profile": configured_profile,
        "profile_reason": profile_reason,
        "persisted_profile": persisted_profile,
        "actual_dimension": actual_dimension,
        "lexical_ready": chunks > 0 and not stale,
        "semantic_ready": bool(cfg.embedding_provider) and profile_matches and dimension_ready
        and chunks > 0 and valid_embedded == chunks and not stale,
        "stale": stale,
        "reason": None,
    }


def _graph_status(root: Path, cfg: Any, vault: Path, owner: bool) -> dict[str, Any]:
    if not owner:
        return {"ready": False, "reason": "not_configured_for_tenant_scope"}
    graph_path = _resolve(root, cfg.graphify_graph)
    if not graph_path.is_file():
        return {"ready": False, "reason": "graph_missing"}
    try:
        from knowledge.graphify import GraphifyProjection

        report = GraphifyProjection(str(graph_path), expected_root=str(vault)).status()
    except Exception:
        return {"ready": False, "reason": "graph_status_unavailable"}
    return {key: value for key, value in report.items() if key != "path"}


def _knowledge_status(root: Path, cfg: Any, scope: str) -> dict[str, Any]:
    owner_vault = _resolve(root, cfg.vault)
    owner = scope == cfg.memory_scope
    vault = owner_vault if owner else (owner_vault / "tenants" / scope).resolve(strict=False)
    try:
        sources = _source_snapshot(vault)
    except OSError:
        sources = {}
        rag = {
            "source_files": None,
            "indexed_files": None,
            "chunks": None,
            "embedded_chunks": None,
            "missing_embeddings": None,
            "configured_profile": "",
            "persisted_profile": None,
            "actual_dimension": None,
            "lexical_ready": False,
            "semantic_ready": False,
            "stale": True,
            "reason": "knowledge_source_unreadable",
        }
    else:
        rag = _read_rag_index(_index_path(root, cfg, vault), sources, cfg)
    wiki = vault / "wiki"
    try:
        all_titles = sorted(
            path.stem
            for path in wiki.glob("*.md")
            if path.is_file() and not path.is_symlink()
        )
    except OSError:
        all_titles = []
    return {
        "pages": len(all_titles),
        "titles": all_titles[:30],
        "rag": rag,
        "graphify": _graph_status(root, cfg, vault, owner),
    }


def _connector_receipts(root: Path, cfg: Any, scope: str) -> tuple[dict[str, Any], str | None]:
    path = _resolve(root, cfg.connector_readiness_db)

    def read(connection: sqlite3.Connection) -> dict[str, Any]:
        rows = connection.execute(
            "SELECT connector,status,evidence,created_at,expires_at "
            "FROM connector_readiness_receipts "
            "WHERE tenant_id=? AND workspace_id=? ORDER BY created_at DESC",
            (scope, cfg.workspace_scope),
        ).fetchall()
        latest: dict[str, Any] = {}
        for row in rows:
            connector = str(row["connector"])
            if connector in latest:
                continue
            evidence = json.loads(str(row["evidence"] or "{}"))
            if not isinstance(evidence, dict):
                raise ValueError("connector readiness evidence must be an object")
            latest[connector] = {
                "status": str(row["status"]),
                "evidence": evidence,
                "expiresAt": float(row["expires_at"]),
            }
        return latest

    return _read_database(path, read)


def _connectors_status(root: Path, cfg: Any, scope: str) -> dict[str, Any]:
    receipts, receipt_reason = _connector_receipts(root, cfg, scope)
    receipts = receipts or {}
    now = time.time()
    ready_ids: list[str] = []
    states: dict[str, str] = {}
    for tool in _BUILTIN_TOOLS:
        connector = str(tool["connector"])
        configured_by = tool["configuredBy"]
        configured = configured_by is None or bool(os.environ.get(str(configured_by), "").strip())
        receipt = receipts.get(connector) if isinstance(receipts, dict) else None
        current = bool(receipt and float(receipt["expiresAt"]) > now)
        state = str(receipt["status"]) if current else ("configured" if configured else "unknown")
        states[connector] = state
        evidence = receipt.get("evidence", {}) if current and isinstance(receipt, dict) else {}
        operational_value = evidence.get("operational", True)
        operational = state == "verified" and operational_value is True
        if configured and operational and tool["supported"] and tool["directCall"]:
            ready_ids.append(str(tool["id"]))

    known = {str(tool["connector"]) for tool in _BUILTIN_TOOLS}
    uninspected = sorted(set(receipts).difference(known)) if isinstance(receipts, dict) else []
    dynamic_configured = bool(
        os.environ.get("PREPENDE_MCP_SERVERS", "").strip()
        or os.environ.get("ENGRAM_MCP_SERVERS", "").strip()
        or (root / "mcp_servers.json").is_file()
    )
    return {
        "tools": len(_BUILTIN_TOOLS),
        "ready": len(ready_ids),
        "ids": [str(tool["id"]) for tool in _BUILTIN_TOOLS],
        "ready_ids": ready_ids,
        "states": states,
        "readiness_status": "observed" if receipt_reason is None else "unavailable",
        "readiness_reason": receipt_reason,
        "uninspected_connectors": uninspected,
        "dynamic_mcp_status": "uninspected" if dynamic_configured else "not_configured",
    }


def collect_context_fast_status(root: Path, scope: str = "") -> dict[str, Any]:
    """Return real local continuity metadata without initializing provider lanes."""

    from kernel.core.config import Config

    cfg = Config()
    selected_scope = str(scope or cfg.memory_scope or "default").strip()
    if not _SCOPE.fullmatch(selected_scope):
        raise ValueError(
            "invalid status scope: must be a lowercase slug ([a-z0-9_-], 1-64 chars)"
        )
    return {
        "scope": selected_scope,
        "model": cfg.provider,
        "model_status": {"initialized": False, "model_call": False},
        "memory": _memory_status(root, cfg, selected_scope),
        "knowledge": _knowledge_status(root, cfg, selected_scope),
        "runs": _runs_status(root, cfg, selected_scope),
        "connectors": _connectors_status(root, cfg, selected_scope),
        "safety": {
            "modelCall": False,
            "embeddingCall": False,
            "connectorProbe": False,
            "memoryWrite": False,
            "indexRebuild": False,
        },
    }
