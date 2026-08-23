#!/usr/bin/env python3
"""Build/finalize Prepende's owner-only Graphify read projection.

Default mode finalizes an existing semantic graph of the owner vault, then
atomically records the manifest and source-directory inventory required by
``knowledge.graphify.GraphifyProjection``. The full semantic extraction remains
the explicit ``/graphify vault`` operation; this wrapper never starts hidden LLM
work or spend. ``--ast-refresh`` is available for code corpora whose output is
inside that corpus.

Install the optional builder once in a clone:
    python3 -m pip install -e '.[graphify]'
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CORPUS = ROOT / "vault"
DEFAULT_OUTPUT = ROOT / "graphify-out" / "knowledge"
sys.path.insert(0, str(ROOT))

from knowledge.graphify import (  # noqa: E402
    GraphifyProjection,
    canonical_source_path,
    corpus_digest,
    evidence_digest,
    graph_hyperedges,
    projection_build_id,
    sha256_file,
    validate_graph_provenance,
)
from prepende_brain.private_fs import (  # noqa: E402
    enforce_private_umask,
    secure_private_tree,
    write_private_text,
)

_NOISE_DIRS = {
    ".git", ".venv", "__pycache__", "node_modules", "graphify-out",
    "dist", "build", "target", "site-packages",
}


def _private_roots(root: Path) -> list[Path]:
    if root.name == "vault":
        return [(root / "tenants").resolve()]
    return [(root / "vault" / "tenants").resolve(), (root / "prepende-data").resolve()]


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _atomic_text(path: Path, value: str) -> None:
    write_private_text(path, value, repair_parent=True)


def _atomic_json(path: Path, value: Any) -> None:
    _atomic_text(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def _owner_detection(root: Path, detection: dict[str, Any]) -> dict[str, Any]:
    """Keep Graphify's ignore semantics and add a hard tenant-vault ceiling."""
    private_roots = _private_roots(root)
    filtered = dict(detection)
    buckets: dict[str, list[str]] = {}
    for kind, raw_files in dict(detection.get("files") or {}).items():
        kept: list[str] = []
        for raw in raw_files or []:
            source = Path(str(raw)).expanduser().resolve()
            if not _inside(source, root):
                continue
            if any(_inside(source, private) for private in private_roots):
                continue
            kept.append(str(source))
        buckets[str(kind)] = kept
    filtered["files"] = buckets
    filtered["total_files"] = sum(len(items) for items in buckets.values())
    return filtered


def _source_tree(root: Path) -> tuple[dict[str, int], dict[str, dict[str, list[str]]]]:
    """Snapshot visible corpus entries while pruning private/runtime trees."""
    private_roots = _private_roots(root)
    directories: dict[str, int] = {}
    entries: dict[str, dict[str, list[str]]] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        directory = Path(dirpath).resolve()
        dirnames[:] = sorted(
            name for name in dirnames
            if not name.startswith(".")
            and name not in _NOISE_DIRS
            and not any(_inside((directory / name).resolve(), private) for private in private_roots)
        )
        visible_files = sorted(name for name in filenames if not name.startswith("."))
        rel = "." if directory == root else directory.relative_to(root).as_posix()
        directories[rel] = directory.stat().st_mtime_ns
        entries[rel] = {"files": visible_files, "directories": list(dirnames)}
    return directories, entries


def _graphify_cache_key(path: Path, hash_root: Path) -> str:
    """Mirror graphifyy 0.6.x's portable content-addressed cache key."""
    raw = path.read_bytes()
    content = raw
    if path.suffix.lower() == ".md":
        text = raw.decode(errors="replace")
        if text.startswith("---"):
            end = text.find("\n---", 3)
            if end != -1:
                content = text[end + 4:].encode()
    digest = hashlib.sha256()
    digest.update(content)
    digest.update(b"\x00")
    try:
        relative = path.resolve().relative_to(hash_root.resolve())
        digest.update(str(relative).encode())
    except ValueError:
        digest.update(str(path.resolve()).encode())
    return digest.hexdigest()


def _cache_candidates(out: Path, source: Path, root: Path) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[Path] = set()
    for hash_root in (root.parent, root):
        key = _graphify_cache_key(source, hash_root)
        paths = (
            ("semantic", out / "cache" / "semantic" / f"{key}.json"),
            ("ast", out / "cache" / "ast" / f"{key}.json"),
            ("ast", out / "cache" / f"{key}.json"),
        )
        for kind, path in paths:
            resolved = path.resolve()
            if resolved in seen or not path.is_file():
                continue
            seen.add(resolved)
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise RuntimeError(f"invalid Graphify {kind} cache entry") from exc
            if not isinstance(payload, dict):
                raise RuntimeError(f"invalid Graphify {kind} cache entry")
            nodes = payload.get("nodes", [])
            edges = payload.get("edges", [])
            hyperedges = payload.get("hyperedges", [])
            if (
                not isinstance(nodes, list)
                or not isinstance(edges, list)
                or not isinstance(hyperedges, list)
            ):
                raise RuntimeError(f"invalid Graphify {kind} cache payload")
            candidates.append({
                "kind": kind,
                "key": key,
                "sha256": sha256_file(path),
                "path": path,
                "payload": payload,
            })
    return candidates


def _stable(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


_NODE_FIELDS = (
    "label", "file_type", "source_location", "source_url", "captured_at",
    "author", "contributor", "rationale",
)
_EDGE_FIELDS = (
    "relation", "confidence", "confidence_score", "source_location", "weight",
)
_HYPEREDGE_FIELDS = (
    "label", "nodes", "relation", "confidence", "confidence_score",
)


def _node_signature(node: dict[str, Any], root: Path) -> str:
    return _stable({
        "id": str(node.get("id") or ""),
        "source_file": canonical_source_path(node.get("source_file"), root),
        **{field: node.get(field) for field in _NODE_FIELDS},
    })


def _edge_endpoints(edge: dict[str, Any], *, original: bool = False) -> tuple[str, str]:
    source_key = "_src" if original and edge.get("_src") is not None else "source"
    target_key = "_tgt" if original and edge.get("_tgt") is not None else "target"
    return str(edge.get(source_key) or ""), str(edge.get(target_key) or "")


def _edge_pair(edge: dict[str, Any], *, directed: bool, original: bool = False) -> tuple[str, str]:
    source, target = _edge_endpoints(edge, original=original)
    return (source, target) if directed else tuple(sorted((source, target)))


def _edge_signature(
    edge: dict[str, Any], root: Path, *, original: bool = False,
) -> str:
    source, target = _edge_endpoints(edge, original=original)
    return _stable({
        "source": source,
        "target": target,
        "source_file": canonical_source_path(edge.get("source_file"), root),
        **{field: edge.get(field) for field in _EDGE_FIELDS},
    })


def _hyperedge_signature(hyperedge: dict[str, Any], root: Path) -> str:
    return _stable({
        "id": str(hyperedge.get("id") or ""),
        "source_file": canonical_source_path(hyperedge.get("source_file"), root),
        **{field: hyperedge.get(field) for field in _HYPEREDGE_FIELDS},
    })


def _previous_source_hashes(out: Path) -> dict[str, str]:
    path = out / "projection.json"
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if int(payload.get("schemaVersion") or 0) != 2:
            return {}
        return {str(key): str(value) for key, value in dict(payload["sourceHashes"]).items()}
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return {}


def _verify_extraction_evidence(
    root: Path,
    out: Path,
    files: list[Path],
) -> dict[str, Any]:
    """Prove the graph came from current content-addressed extraction caches."""
    graph_path = out / "graph.json"
    try:
        graph = json.loads(graph_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("Graphify graph is missing or invalid") from exc
    if not isinstance(graph, dict):
        raise RuntimeError("Graphify graph is invalid")
    relative_files = [path.relative_to(root).as_posix() for path in files]
    provenance = validate_graph_provenance(
        graph,
        root=root,
        allowed_files=relative_files,
        ignored_roots=_private_roots(root),
    )
    if not provenance["ok"]:
        raise RuntimeError(
            "Graphify graph provenance escapes the owner manifest "
            f"({provenance['invalidReferences']} invalid reference(s))"
        )

    previous_hashes = _previous_source_hashes(out)
    source_hashes = {rel: sha256_file(path) for rel, path in zip(relative_files, files)}
    evidence_entries: dict[str, list[dict[str, str]]] = {}
    cached_nodes: list[dict[str, Any]] = []
    cached_edges: list[dict[str, Any]] = []
    cached_hyperedges: list[dict[str, Any]] = []
    for rel, source in zip(relative_files, files):
        caches = _cache_candidates(out, source, root)
        if not caches:
            raise RuntimeError(
                "current content-addressed extraction cache is missing for "
                f"{rel}; rerun /graphify on the owner corpus before finalizing"
            )
        if previous_hashes.get(rel) != source_hashes[rel]:
            newest_cache = max(item["path"].stat().st_mtime_ns for item in caches)
            if newest_cache < source.stat().st_mtime_ns:
                raise RuntimeError(
                    "extraction cache predates changed source content for "
                    f"{rel}; clear that cache entry and rerun /graphify"
                )
        evidence_entries[rel] = sorted(
            ({
                "kind": str(item["kind"]),
                "key": str(item["key"]),
                "sha256": str(item["sha256"]),
            } for item in caches),
            key=lambda item: (item["kind"], item["key"], item["sha256"]),
        )
        for item in caches:
            payload = item["payload"]
            cached_nodes.extend(payload.get("nodes", []))
            cached_edges.extend(payload.get("edges", []))
            cached_hyperedges.extend(payload.get("hyperedges", []))

    graph_nodes = graph.get("nodes")
    graph_edges = graph.get("links", graph.get("edges"))
    if not isinstance(graph_nodes, list) or not isinstance(graph_edges, list):
        raise RuntimeError("Graphify graph is missing nodes or edges")
    directed = bool(graph.get("directed"))

    graph_node_ids = {str(node.get("id") or "") for node in graph_nodes}
    cache_node_ids = {str(node.get("id") or "") for node in cached_nodes}
    if graph_node_ids != cache_node_ids:
        raise RuntimeError("graph node inventory does not match current extraction caches")
    cache_node_signatures = {_node_signature(node, root) for node in cached_nodes}
    if any(_node_signature(node, root) not in cache_node_signatures for node in graph_nodes):
        raise RuntimeError("graph node attributes do not match current extraction caches")
    cache_node_sources: dict[str, set[str | None]] = {}
    for node in cached_nodes:
        cache_node_sources.setdefault(str(node.get("id") or ""), set()).add(
            canonical_source_path(node.get("source_file"), root)
        )
    for node in graph_nodes:
        node_id = str(node.get("id") or "")
        for item in node.get("provenance") or []:
            source = canonical_source_path(item.get("source_file"), root)
            if source not in cache_node_sources.get(node_id, set()):
                raise RuntimeError("graph node provenance does not match extraction caches")

    graph_pairs = {
        _edge_pair(edge, directed=directed, original=True) for edge in graph_edges
    }
    cache_pairs = {
        _edge_pair(edge, directed=directed) for edge in cached_edges
    }
    if graph_pairs != cache_pairs:
        raise RuntimeError("graph adjacency does not match current extraction caches")
    cache_edge_signatures = {_edge_signature(edge, root) for edge in cached_edges}
    for edge in graph_edges:
        signatures = {_edge_signature(edge, root, original=True)}
        if not directed and edge.get("_src") is None:
            reversed_edge = dict(edge, source=edge.get("target"), target=edge.get("source"))
            signatures.add(_edge_signature(reversed_edge, root))
        if signatures.isdisjoint(cache_edge_signatures):
            raise RuntimeError("graph edge attributes do not match current extraction caches")

    graph_hypers = graph_hyperedges(graph)
    graph_hyper_ids = {str(item.get("id") or "") for item in graph_hypers}
    cache_hyper_ids = {str(item.get("id") or "") for item in cached_hyperedges}
    if graph_hyper_ids != cache_hyper_ids:
        raise RuntimeError("graph hyperedge inventory does not match extraction caches")
    cache_hyper_signatures = {
        _hyperedge_signature(item, root) for item in cached_hyperedges
    }
    if any(
        _hyperedge_signature(item, root) not in cache_hyper_signatures
        for item in graph_hypers
    ):
        raise RuntimeError("graph hyperedges do not match current extraction caches")

    graph_sha256 = sha256_file(graph_path)
    return {
        "sourceHashes": source_hashes,
        "corpusSha256": corpus_digest(source_hashes),
        "graphSha256": graph_sha256,
        "extractionEvidence": {
            "type": "graphify-content-addressed-cache",
            "entries": evidence_entries,
            "sha256": evidence_digest(evidence_entries),
        },
    }


def finalize_projection(
    root: str | Path,
    detection: dict[str, Any],
    *,
    output_dir: str | Path | None = None,
    mode: str,
    python_executable: str | None = None,
    graphify_version: str = "unknown",
) -> dict[str, Any]:
    """Seal a cache-proven graph and return verified projection readiness."""
    resolved_root = Path(root).expanduser().resolve()
    out = (
        Path(output_dir).expanduser().resolve()
        if output_dir is not None
        else resolved_root / "graphify-out"
    )
    if out in {resolved_root, ROOT.resolve()}:
        raise RuntimeError("Graphify output must be a dedicated private state directory")
    if out.exists():
        secure_private_tree(out)
    graph_path = out / "graph.json"
    if not graph_path.is_file():
        raise FileNotFoundError(f"Graphify did not produce {graph_path}")
    filtered = _owner_detection(resolved_root, detection)
    files = sorted({
        Path(raw).expanduser().resolve()
        for items in filtered["files"].values()
        for raw in items
    })
    manifest = {str(path): path.stat().st_mtime for path in files if path.is_file()}
    relative_files = [
        path.relative_to(resolved_root).as_posix() for path in files if path.is_file()
    ]
    if not relative_files:
        raise RuntimeError("owner corpus detection returned no source files")

    # This check happens before any manifest/projection write. Merely scanning
    # today's files cannot bless yesterday's graph: every graph claim must be
    # present in a cache entry addressed by the current source content.
    attestation = _verify_extraction_evidence(resolved_root, out, files)
    extraction = attestation["extractionEvidence"]
    build_id = projection_build_id(
        attestation["corpusSha256"],
        attestation["graphSha256"],
        extraction["sha256"],
    )

    secure_private_tree(out)
    directories, directory_entries = _source_tree(resolved_root)
    projection = {
        "schemaVersion": 2,
        "projectionKind": "knowledge",
        "root": str(resolved_root),
        "scope": "owner",
        "mode": mode,
        "graphifyVersion": graphify_version,
        "files": relative_files,
        "directories": directories,
        "directoryEntries": directory_entries,
        "ignoredRoots": [str(path) for path in _private_roots(resolved_root)],
        "graphifyIgnorePatterns": int(filtered.get("graphifyignore_patterns") or 0),
        "sourceHashes": attestation["sourceHashes"],
        "corpusSha256": attestation["corpusSha256"],
        "graphSha256": attestation["graphSha256"],
        "extractionEvidence": extraction,
        "buildId": build_id,
    }
    graph_data = json.loads(graph_path.read_text(encoding="utf-8"))
    projection_manifest = {
        "schemaVersion": "prepende-projection-manifest-v1",
        "kind": "knowledge",
        "sourceRoot": str(resolved_root),
        "revision": attestation["corpusSha256"],
        "projectionSchema": "graphify-projection-v2",
        "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "counts": {
            "sources": len(relative_files),
            "nodes": len(graph_data.get("nodes") or []),
            "edges": len(graph_data.get("links", graph_data.get("edges")) or []),
            "hyperedges": len(graph_hyperedges(graph_data)),
        },
        "checksum": "sha256:" + attestation["graphSha256"],
    }
    # projection.json is the commit marker. If a preceding atomic write is
    # interrupted, the old projection remains and its hashes fail closed.
    _atomic_text(out / ".graphify_root", str(resolved_root) + "\n")
    _atomic_text(out / ".graphify_python", python_executable or sys.executable)
    _atomic_json(out / "manifest.json", manifest)
    _atomic_json(out / "projection-manifest.json", projection_manifest)
    if sha256_file(graph_path) != attestation["graphSha256"]:
        raise RuntimeError("graph changed while finalization was in progress")
    _atomic_json(out / "projection.json", projection)
    status = GraphifyProjection(
        str(graph_path), expected_root=resolved_root
    ).status()
    if not status.get("ready"):
        raise RuntimeError(f"Graphify projection failed readiness: {status}")
    return {
        "ok": True,
        "root": str(resolved_root),
        "graph": str(graph_path),
        "buildId": build_id,
        "detectedFiles": int(filtered.get("total_files") or 0),
        "status": status,
    }


def main() -> int:
    enforce_private_umask()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", default=str(DEFAULT_CORPUS),
        help="corpus root (default: repository owner vault)",
    )
    parser.add_argument(
        "--out", default=str(DEFAULT_OUTPUT),
        help="Graphify output directory (default: repository graphify-out/knowledge)",
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--manifest-only",
        action="store_true",
        help=(
            "finalize an existing graph only when current content-addressed "
            "extraction caches prove it (the default)"
        ),
    )
    modes.add_argument(
        "--ast-refresh",
        action="store_true",
        help="run deterministic code extraction before finalizing (code corpora only)",
    )
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()
    root = Path(args.root).expanduser().resolve()
    out = Path(args.out).expanduser().resolve()
    if not root.is_dir():
        print(f"refresh_graphify: root not found: {root}", file=sys.stderr)
        return 2
    try:
        from graphify.detect import detect
        from graphify.watch import _rebuild_code
    except ImportError:
        print(
            "refresh_graphify: optional builder missing; install with "
            "python3 -m pip install -e '.[graphify]'",
            file=sys.stderr,
        )
        return 2

    mode = "existing_graph_finalized"
    if args.ast_refresh:
        native_out = root / "graphify-out"
        if out != native_out:
            print(
                "refresh_graphify: --ast-refresh requires --out <root>/graphify-out; "
                "the owner vault is semantic Markdown, so build it with /graphify vault",
                file=sys.stderr,
            )
            return 2
        if not _rebuild_code(root):
            print("refresh_graphify: structural graph rebuild failed", file=sys.stderr)
            return 1
        mode = "ast_refresh"
    try:
        installed = version("graphifyy")
    except PackageNotFoundError:
        installed = "unknown"
    try:
        result = finalize_projection(
            root,
            detect(root),
            output_dir=out,
            mode=mode,
            python_executable=sys.executable,
            graphify_version=installed,
        )
    except Exception as exc:
        print(f"refresh_graphify: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    if args.as_json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        status = result["status"]
        print(
            "Graphify ready: "
            f"{status['nodes']} nodes, {status['edges']} edges, "
            f"{status['sourceFiles']} owner source files ({status['mode']})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
