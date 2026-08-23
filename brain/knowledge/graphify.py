"""Read-only bridge from a completed Graphify projection into owner recall.

Graphify is an optional projection, never a source of truth. This adapter is
stdlib-only and refuses missing, malformed, wrong-root, or stale artifacts so
an old graph cannot masquerade as current brain knowledge. It is wired only
into the owner brain; tenant loops never receive a repository/corpus graph.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable

_TERM = re.compile(r"[a-z0-9]+")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MIN_EDGE_CONFIDENCE = 0.6
_PROJECTION_SCHEMA = 2


def _terms(text: str) -> list[str]:
    return [t for t in _TERM.findall(str(text or "").lower()) if len(t) >= 2]


def _clean(value: Any, limit: int = 240) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit].rstrip()


def sha256_file(path: str | Path) -> str:
    """Return the exact-byte SHA-256 used by projection attestations."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_digest(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def corpus_digest(source_hashes: dict[str, str]) -> str:
    """Bind an inventory and every exact source byte into one digest."""
    return _json_digest({str(path): str(value) for path, value in source_hashes.items()})


def evidence_digest(entries: dict[str, Any]) -> str:
    """Bind the content-addressed extraction-cache receipts."""
    return _json_digest(entries)


def projection_build_id(
    corpus_sha256: str,
    graph_sha256: str,
    extraction_evidence_sha256: str,
) -> str:
    """Stable identity for one exact corpus, extraction receipt, and graph."""
    return _json_digest({
        "schema": "prepende-graphify-projection-v2",
        "corpusSha256": corpus_sha256,
        "graphSha256": graph_sha256,
        "extractionEvidenceSha256": extraction_evidence_sha256,
    })


def canonical_source_path(raw: Any, root: str | Path) -> str | None:
    """Normalize Graphify provenance to a safe path below ``root``.

    Graphify may emit either corpus-relative paths (``wiki/x.md``), paths
    relative to the repository (``vault/wiki/x.md``), or absolute paths.  Any
    traversal or absolute path outside the corpus is rejected.
    """
    text = str(raw or "").strip().replace("\\", "/")
    if not text:
        return None
    resolved_root = Path(root).expanduser().resolve()
    source = Path(text).expanduser()
    if source.is_absolute():
        try:
            return source.resolve().relative_to(resolved_root).as_posix()
        except (OSError, ValueError):
            return None
    parts = list(Path(text).parts)
    if any(part == ".." for part in parts):
        return None
    if parts and parts[0] == resolved_root.name:
        parts = parts[1:]
    if not parts:
        return None
    candidate = resolved_root.joinpath(*parts).resolve()
    try:
        return candidate.relative_to(resolved_root).as_posix()
    except (OSError, ValueError):
        return None


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def graph_hyperedges(data: dict[str, Any]) -> list[Any]:
    raw = data.get("hyperedges")
    if raw is None and isinstance(data.get("graph"), dict):
        raw = data["graph"].get("hyperedges", [])
    return raw if isinstance(raw, list) else []


def validate_graph_provenance(
    data: Any,
    *,
    root: str | Path,
    allowed_files: Iterable[str],
    ignored_roots: Iterable[str | Path] = (),
) -> dict[str, Any]:
    """Fail closed when any graph claim escapes the certified owner corpus."""
    resolved_root = Path(root).expanduser().resolve()
    allowed = {str(path).replace("\\", "/") for path in allowed_files}
    ignored = [Path(path).expanduser().resolve() for path in ignored_roots]
    errors: list[str] = []

    def check_source(raw: Any, kind: str) -> None:
        rel = canonical_source_path(raw, resolved_root)
        if rel is None or rel not in allowed:
            errors.append(kind)
            return
        candidate = (resolved_root / rel).resolve()
        if any(_inside(candidate, private) for private in ignored):
            errors.append(kind)

    if not isinstance(data, dict):
        return {"ok": False, "reason": "graph_provenance_invalid", "invalidReferences": 1}
    nodes = data.get("nodes")
    edges = data.get("links", data.get("edges"))
    if not isinstance(nodes, list) or not isinstance(edges, list):
        return {"ok": False, "reason": "graph_provenance_invalid", "invalidReferences": 1}
    node_ids = {
        str(node.get("id")) for node in nodes
        if isinstance(node, dict) and node.get("id")
    }
    for node in nodes:
        if not isinstance(node, dict) or not node.get("id") or not node.get("label"):
            errors.append("node_schema")
            continue
        check_source(node.get("source_file"), "node_source")
        provenance = node.get("provenance")
        if provenance is not None:
            if not isinstance(provenance, list):
                errors.append("node_provenance_schema")
            else:
                for item in provenance:
                    if not isinstance(item, dict):
                        errors.append("node_provenance_schema")
                    else:
                        check_source(item.get("source_file"), "node_provenance_source")
    for edge in edges:
        if not isinstance(edge, dict):
            errors.append("edge_schema")
            continue
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        if source not in node_ids or target not in node_ids:
            errors.append("edge_endpoint")
        check_source(edge.get("source_file"), "edge_source")
    for hyperedge in graph_hyperedges(data):
        if not isinstance(hyperedge, dict) or not hyperedge.get("id"):
            errors.append("hyperedge_schema")
            continue
        members = hyperedge.get("nodes")
        if not isinstance(members, list) or any(str(item) not in node_ids for item in members):
            errors.append("hyperedge_endpoint")
        check_source(hyperedge.get("source_file"), "hyperedge_source")
    return {
        "ok": not errors,
        "reason": "current" if not errors else "graph_provenance_invalid",
        "invalidReferences": len(errors),
    }


class GraphifyProjection:
    """Load and query the owner knowledge projection without importing Graphify."""

    def __init__(
        self,
        graph_path: str = "./graphify-out/knowledge/graph.json",
        *,
        expected_root: str | Path | None = None,
        expected_kind: str = "knowledge",
    ) -> None:
        self.path = Path(graph_path).expanduser().resolve()
        # Bind the artifact to one checkout/corpus. A graph copied from a
        # still-present sibling worktree must never pass as current here.
        self.expected_root = (
            Path(expected_root).expanduser().resolve()
            if expected_root is not None
            else self.path.parent.parent.parent.resolve()
        )
        self.expected_kind = expected_kind
        self._nodes: dict[str, dict[str, Any]] = {}
        self._edges: list[dict[str, Any]] = []
        self._adj: dict[str, list[dict[str, Any]]] = {}
        self._raw_graph: dict[str, Any] = {}
        self._signature: tuple[int, ...] | None = None
        self._schema_error = ""

    def _paths(self) -> tuple[Path, Path, Path, Path, Path]:
        out = self.path.parent
        return (
            out / "manifest.json",
            out / ".graphify_root",
            out / ".graphify_python",
            out / "projection.json",
            out / "projection-manifest.json",
        )

    @staticmethod
    def _relative(path: Path, root: Path) -> str:
        rel = path.relative_to(root)
        return "." if str(rel) in ("", ".") else rel.as_posix()

    @staticmethod
    def _inside(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False

    def _freshness(self) -> dict[str, Any]:
        (
            manifest_path,
            root_path,
            _,
            projection_path,
            frontier_manifest_path,
        ) = self._paths()
        if not manifest_path.is_file() or not root_path.is_file():
            return {
                "ready": False,
                "reason": "manifest_or_root_missing",
                "staleFiles": 0,
                "newFileCheck": "unavailable",
            }
        if not projection_path.is_file():
            return {
                "ready": False,
                "reason": "projection_metadata_missing",
                "staleFiles": 0,
                "newFileCheck": "unavailable",
            }
        if not frontier_manifest_path.is_file():
            return {
                "ready": False,
                "reason": "projection_manifest_missing",
                "staleFiles": 0,
                "newFileCheck": "unavailable",
            }
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            root = Path(root_path.read_text(encoding="utf-8").strip()).expanduser().resolve()
            projection = json.loads(projection_path.read_text(encoding="utf-8"))
            frontier_manifest = json.loads(
                frontier_manifest_path.read_text(encoding="utf-8")
            )
        except Exception:
            return {
                "ready": False,
                "reason": "manifest_or_root_invalid",
                "staleFiles": 0,
                "newFileCheck": "unavailable",
            }
        if (
            not isinstance(manifest, dict)
            or not isinstance(projection, dict)
            or not isinstance(frontier_manifest, dict)
            or not root.is_dir()
        ):
            return {
                "ready": False,
                "reason": "manifest_or_root_invalid",
                "staleFiles": 0,
                "newFileCheck": "unavailable",
            }
        if root != self.expected_root:
            return {
                "ready": False,
                "reason": "root_mismatch",
                "staleFiles": 0,
                "newFileCheck": "unavailable",
            }
        try:
            schema_version = int(projection["schemaVersion"])
            metadata_root = Path(str(projection["root"])).expanduser().resolve()
            raw_expected_files = [str(item).replace("\\", "/") for item in projection["files"]]
            expected_files = set(raw_expected_files)
            expected_dirs = {
                str(path): int(mtime)
                for path, mtime in dict(projection["directories"]).items()
            }
            expected_entries = {
                str(path): {
                    "files": sorted(str(item) for item in dict(value).get("files", [])),
                    "directories": sorted(
                        str(item) for item in dict(value).get("directories", [])
                    ),
                }
                for path, value in dict(projection["directoryEntries"]).items()
            }
            ignored_roots = [
                Path(str(path)).expanduser().resolve()
                for path in projection.get("ignoredRoots", [])
            ]
            source_hashes = {
                str(path).replace("\\", "/"): str(value)
                for path, value in dict(projection["sourceHashes"]).items()
            }
            expected_corpus_digest = str(projection["corpusSha256"])
            expected_graph_digest = str(projection["graphSha256"])
            build_id = str(projection["buildId"])
            evidence = dict(projection["extractionEvidence"])
            evidence_entries = dict(evidence["entries"])
            expected_evidence_digest = str(evidence["sha256"])
        except (KeyError, TypeError, ValueError):
            return {
                "ready": False,
                "reason": "projection_metadata_invalid",
                "staleFiles": 0,
                "newFileCheck": "unavailable",
            }
        if schema_version != _PROJECTION_SCHEMA:
            return {
                "ready": False,
                "reason": "projection_schema_unsupported",
                "staleFiles": 0,
                "newFileCheck": "unavailable",
            }
        if metadata_root != root:
            return {
                "ready": False,
                "reason": "projection_root_mismatch",
                "staleFiles": 0,
                "newFileCheck": "unavailable",
            }
        if (
            str(projection.get("scope")) != "owner"
            or str(projection.get("projectionKind")) != self.expected_kind
            or len(raw_expected_files) != len(expected_files)
            or any(canonical_source_path(path, root) != path for path in expected_files)
            or set(source_hashes) != expected_files
            or set(evidence_entries) != expected_files
            or any(not _SHA256.fullmatch(value) for value in source_hashes.values())
            or not _SHA256.fullmatch(expected_corpus_digest)
            or not _SHA256.fullmatch(expected_graph_digest)
            or not _SHA256.fullmatch(expected_evidence_digest)
            or not _SHA256.fullmatch(build_id)
        ):
            return {
                "ready": False,
                "reason": "projection_metadata_invalid",
                "staleFiles": 0,
                "newFileCheck": "unavailable",
            }
        try:
            manifest_root = Path(
                str(frontier_manifest["sourceRoot"])
            ).expanduser().resolve()
            manifest_counts = dict(frontier_manifest["counts"])
        except (KeyError, TypeError, ValueError):
            return {
                "ready": False,
                "reason": "projection_manifest_invalid",
                "staleFiles": 0,
                "newFileCheck": "unavailable",
            }
        if (
            frontier_manifest.get("schemaVersion")
            != "prepende-projection-manifest-v1"
            or frontier_manifest.get("kind") != self.expected_kind
            or manifest_root != root
            or frontier_manifest.get("revision") != expected_corpus_digest
            or frontier_manifest.get("projectionSchema")
            != "graphify-projection-v2"
            or frontier_manifest.get("checksum")
            != "sha256:" + expected_graph_digest
            or not str(frontier_manifest.get("generatedAt") or "").strip()
            or set(manifest_counts)
            != {"sources", "nodes", "edges", "hyperedges"}
            or any(
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
                for value in manifest_counts.values()
            )
            or manifest_counts.get("sources") != len(expected_files)
            or manifest_counts.get("nodes") != len(self._nodes)
            or manifest_counts.get("edges") != len(self._edges)
            or manifest_counts.get("hyperedges")
            != len(graph_hyperedges(self._raw_graph))
        ):
            return {
                "ready": False,
                "reason": "projection_manifest_invalid",
                "staleFiles": 0,
                "newFileCheck": "unavailable",
            }
        for records in evidence_entries.values():
            if not isinstance(records, list) or not records:
                return {
                    "ready": False,
                    "reason": "extraction_evidence_invalid",
                    "staleFiles": 0,
                    "newFileCheck": "unavailable",
                }
            for record in records:
                if (
                    not isinstance(record, dict)
                    or record.get("kind") not in {"ast", "semantic"}
                    or not _SHA256.fullmatch(str(record.get("key") or ""))
                    or not _SHA256.fullmatch(str(record.get("sha256") or ""))
                ):
                    return {
                        "ready": False,
                        "reason": "extraction_evidence_invalid",
                        "staleFiles": 0,
                        "newFileCheck": "unavailable",
                    }
        if evidence_digest(evidence_entries) != expected_evidence_digest:
            return {
                "ready": False,
                "reason": "extraction_evidence_digest_mismatch",
                "staleFiles": 0,
                "newFileCheck": "verified",
            }

        mtime_drift = 0
        outside = 0
        manifest_files: set[str] = set()
        for raw_path, expected in manifest.items():
            source = Path(str(raw_path)).expanduser()
            if not source.is_absolute():
                source = root / source
            try:
                resolved = source.resolve()
                resolved.relative_to(root)
                manifest_files.add(self._relative(resolved, root))
            except (OSError, ValueError):
                outside += 1
                continue
            try:
                if abs(resolved.stat().st_mtime - float(expected)) > 1e-6:
                    mtime_drift += 1
            except (OSError, TypeError, ValueError):
                mtime_drift += 1
        if outside:
            return {
                "ready": False,
                "reason": "manifest_outside_root",
                "staleFiles": 0,
                "outsideFiles": outside,
                "newFileCheck": "verified",
            }
        if manifest_files != expected_files:
            return {
                "ready": False,
                "reason": "manifest_inventory_mismatch",
                "staleFiles": 0,
                "outsideFiles": 0,
                "newFileCheck": "verified",
            }
        current_hashes: dict[str, str] = {}
        changed_hashes = 0
        for rel in sorted(expected_files):
            source = root / rel
            try:
                current_hashes[rel] = sha256_file(source)
            except OSError:
                changed_hashes += 1
                continue
            if current_hashes[rel] != source_hashes[rel]:
                changed_hashes += 1
        if changed_hashes:
            return {
                "ready": False,
                "reason": "source_hash_mismatch",
                "staleFiles": changed_hashes,
                "outsideFiles": 0,
                "newFileCheck": "verified",
            }
        if corpus_digest(current_hashes) != expected_corpus_digest:
            return {
                "ready": False,
                "reason": "corpus_digest_mismatch",
                "staleFiles": 0,
                "outsideFiles": 0,
                "newFileCheck": "verified",
            }

        provenance = validate_graph_provenance(
            self._raw_graph,
            root=root,
            allowed_files=expected_files,
            ignored_roots=ignored_roots,
        )
        if not provenance["ok"]:
            return {
                "ready": False,
                "reason": provenance["reason"],
                "staleFiles": 0,
                "invalidProvenance": provenance["invalidReferences"],
                "newFileCheck": "verified",
            }
        try:
            current_graph_digest = sha256_file(self.path)
        except OSError:
            current_graph_digest = ""
        if current_graph_digest != expected_graph_digest:
            return {
                "ready": False,
                "reason": "graph_digest_mismatch",
                "staleFiles": 0,
                "newFileCheck": "verified",
            }
        if projection_build_id(
            expected_corpus_digest,
            expected_graph_digest,
            expected_evidence_digest,
        ) != build_id:
            return {
                "ready": False,
                "reason": "build_identity_mismatch",
                "staleFiles": 0,
                "newFileCheck": "verified",
            }
        # Existing-file mtimes cannot detect a newly-created source. The
        # repo-local refresh wrapper records source-directory mtimes after
        # Graphify detection (which honors .graphifyignore). A create/delete in
        # that tree makes the projection fail closed until refreshed.
        changed_dirs = 0
        for raw_dir, expected_mtime in expected_dirs.items():
            candidate = root if raw_dir == "." else (root / raw_dir)
            try:
                if not candidate.is_dir():
                    changed_dirs += 1
                    continue
                if candidate.stat().st_mtime_ns == expected_mtime:
                    continue
                current_files: list[str] = []
                current_dirs: list[str] = []
                for item in candidate.iterdir():
                    if item.name.startswith("."):
                        continue
                    resolved = item.resolve()
                    if any(self._inside(resolved, ignored) for ignored in ignored_roots):
                        continue
                    if item.is_dir():
                        if item.name not in {
                            ".git", ".venv", "__pycache__", "node_modules",
                            "graphify-out", "dist", "build", "target", "site-packages",
                        }:
                            current_dirs.append(item.name)
                    elif item.is_file():
                        current_files.append(item.name)
                current = {
                    "files": sorted(current_files),
                    "directories": sorted(current_dirs),
                }
                if current != expected_entries.get(raw_dir):
                    changed_dirs += 1
            except OSError:
                changed_dirs += 1
        if changed_dirs:
            return {
                "ready": False,
                "reason": "source_set_changed",
                "staleFiles": 0,
                "changedDirectories": changed_dirs,
                "outsideFiles": 0,
                "newFileCheck": "verified",
            }
        return {
            "ready": True,
            "reason": "current",
            "staleFiles": 0,
            "mtimeDrift": mtime_drift,
            "outsideFiles": 0,
            "newFileCheck": "verified",
            "mode": str(projection.get("mode") or "unknown"),
            "scope": str(projection.get("scope") or "owner"),
            "sourceFiles": len(expected_files),
            "buildId": build_id,
            "attestation": str(evidence.get("type") or "unknown"),
        }

    def _load(self) -> None:
        graph_mtime = self.path.stat().st_mtime_ns if self.path.is_file() else -1
        (
            manifest_path,
            root_path,
            _,
            projection_path,
            frontier_manifest_path,
        ) = self._paths()
        manifest_mtime = manifest_path.stat().st_mtime_ns if manifest_path.is_file() else -1
        root_mtime = root_path.stat().st_mtime_ns if root_path.is_file() else -1
        projection_mtime = projection_path.stat().st_mtime_ns if projection_path.is_file() else -1
        frontier_manifest_mtime = (
            frontier_manifest_path.stat().st_mtime_ns
            if frontier_manifest_path.is_file()
            else -1
        )
        signature = (
            graph_mtime,
            manifest_mtime,
            root_mtime,
            projection_mtime,
            frontier_manifest_mtime,
        )
        if signature == self._signature:
            return
        self._signature = signature
        self._nodes, self._edges, self._adj, self._raw_graph = {}, [], {}, {}
        self._schema_error = ""
        if not self.path.is_file():
            self._schema_error = "graph_missing"
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            raw_nodes = data.get("nodes") if isinstance(data, dict) else None
            raw_edges = data.get("links", data.get("edges", [])) if isinstance(data, dict) else None
            if not isinstance(raw_nodes, list) or not isinstance(raw_edges, list):
                raise ValueError("nodes and links/edges arrays are required")
            for node in raw_nodes:
                if not isinstance(node, dict) or not node.get("id") or not node.get("label"):
                    raise ValueError("every node requires id and label")
                nid = str(node["id"])
                if nid in self._nodes:
                    raise ValueError(f"duplicate node id: {nid}")
                self._nodes[nid] = dict(node)
                self._adj[nid] = []
            for edge in raw_edges:
                if not isinstance(edge, dict):
                    raise ValueError("every edge must be an object")
                source, target = str(edge.get("source", "")), str(edge.get("target", ""))
                if source not in self._nodes or target not in self._nodes:
                    raise ValueError("edge endpoint is missing from nodes")
                normalized = dict(edge, source=source, target=target)
                self._edges.append(normalized)
                self._adj[source].append(normalized)
                self._adj[target].append(normalized)
            self._raw_graph = data
        except (OSError, json.JSONDecodeError, ValueError, TypeError) as exc:
            self._schema_error = f"graph_invalid: {exc}"
            self._nodes, self._edges, self._adj, self._raw_graph = {}, [], {}, {}

    def status(self) -> dict[str, Any]:
        self._load()
        freshness = self._freshness()
        ready = bool(self._nodes) and not self._schema_error and freshness["ready"]
        return {
            "ready": ready,
            "path": str(self.path),
            "nodes": len(self._nodes),
            "edges": len(self._edges),
            "reason": self._schema_error or freshness["reason"],
            "staleFiles": freshness.get("staleFiles", 0),
            "mtimeDrift": freshness.get("mtimeDrift", 0),
            "changedDirectories": freshness.get("changedDirectories", 0),
            "invalidProvenance": freshness.get("invalidProvenance", 0),
            "newFileCheck": freshness.get("newFileCheck", "unavailable"),
            "mode": freshness.get("mode"),
            "scope": freshness.get("scope"),
            "sourceFiles": freshness.get("sourceFiles", 0),
            "buildId": freshness.get("buildId"),
            "attestation": freshness.get("attestation"),
        }

    def _node_score(
        self,
        node: dict[str, Any],
        query_terms: Iterable[str],
        source_hints: Iterable[str],
    ) -> float:
        label = _clean(node.get("label"), 500).lower()
        source = _clean(node.get("source_file"), 500).lower()
        node_id = str(node.get("id", "")).lower()
        hints = [str(h).replace("\\", "/").lower() for h in source_hints if h]
        score = 0.0
        for term in query_terms:
            if term in label:
                score += 2.0
            elif term in node_id:
                score += 1.5
            elif term in source:
                score += 0.5
        if any(source.endswith(h) or h.endswith(source) for h in hints):
            score += 3.0
        return score

    @staticmethod
    def _confidence(edge: dict[str, Any]) -> float:
        raw = edge.get("confidence_score")
        if isinstance(raw, (int, float)):
            return max(0.0, min(1.0, float(raw)))
        return {"EXTRACTED": 1.0, "INFERRED": 0.6}.get(str(edge.get("confidence", "")), 0.0)

    def _edge_text(self, edge: dict[str, Any]) -> str:
        source = self._nodes[edge["source"]]
        target = self._nodes[edge["target"]]
        conf = self._confidence(edge)
        relation = _clean(edge.get("relation"), 80) or "related_to"
        evidence = str(edge.get("confidence") or "UNSCORED")
        src_file = _clean(edge.get("source_file") or source.get("source_file"), 180)
        src_loc = _clean(edge.get("source_location") or source.get("source_location"), 80)
        where = f"; source {src_file}" if src_file else ""
        if src_loc:
            where += f":{src_loc}"
        return (
            f"{_clean(source.get('label'))} --{relation} [{evidence} {conf:.2f}]--> "
            f"{_clean(target.get('label'))}{where}"
        )

    async def recall(
        self,
        query: str,
        *,
        k: int = 2,
        neighbor_k: int = 2,
        direct_k: int | None = None,
        source_hints: Iterable[str] = (),
    ) -> list[dict[str, Any]]:
        self._load()
        if self._schema_error or not self._freshness().get("ready"):
            return []
        if direct_k is not None:
            k = direct_k
        terms = _terms(query)
        ranked = sorted(
            ((self._node_score(node, terms, source_hints), nid) for nid, node in self._nodes.items()),
            reverse=True,
        )
        seeds = [nid for score, nid in ranked if score > 0][:max(0, k)]
        if not seeds:
            return []
        out: list[dict[str, Any]] = []
        for nid in seeds:
            node = self._nodes[nid]
            source = _clean(node.get("source_file"), 180)
            location = _clean(node.get("source_location"), 80)
            where = f"; source {source}" if source else ""
            if location:
                where += f":{location}"
            rationale = _clean(node.get("rationale"), 280)
            text = f"(Graphify node; projection current{where}) {_clean(node.get('label'))}"
            if rationale:
                text += f" — {rationale}"
            out.append({
                "content": text,
                "source": "graphify",
                "node": nid,
                "source_file": source,
                "source_location": location,
                "confidence": "NODE",
                "confidence_score": 1.0,
            })

        candidates: list[tuple[float, str, dict[str, Any]]] = []
        seen: set[str] = set(seeds)
        for seed in seeds:
            for edge in self._adj.get(seed, ()):
                confidence = self._confidence(edge)
                if confidence < _MIN_EDGE_CONFIDENCE:
                    continue
                other = edge["target"] if edge["source"] == seed else edge["source"]
                if other in seen:
                    continue
                score = confidence + self._node_score(self._nodes[other], terms, source_hints) * 0.1
                candidates.append((score, other, edge))
        candidates.sort(key=lambda item: (-item[0], item[1]))
        for _, other, edge in candidates[:max(0, neighbor_k)]:
            seen.add(other)
            out.append({
                "content": f"(Graphify edge; projection current) {self._edge_text(edge)}",
                "source": "graphify_graph",
                "node": other,
                "relation": edge.get("relation"),
                "source_file": _clean(
                    edge.get("source_file") or self._nodes[other].get("source_file"), 180
                ),
                "source_location": _clean(
                    edge.get("source_location") or self._nodes[other].get("source_location"), 80
                ),
                "confidence": str(edge.get("confidence") or "UNSCORED"),
                "confidence_score": self._confidence(edge),
            })
        return out
