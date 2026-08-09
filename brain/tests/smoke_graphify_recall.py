"""Graphify owner projection: current, bounded, clone-safe, and tenant-safe."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from interface import prepende_runtime as v1_api  # noqa: E402
from kernel.core.recall import unified_recall  # noqa: E402
from kernel.core.strategist import RulesStrategist  # noqa: E402
from knowledge.graphify import (  # noqa: E402
    GraphifyProjection,
    projection_build_id,
    sha256_file,
)
from models.echo import EchoGateway  # noqa: E402
from scripts.refresh_graphify import (  # noqa: E402
    _graphify_cache_key,
    finalize_projection,
)


class FakeKnowledge:
    async def search(self, query, k=8):
        return [{
            "page": "rag-design",
            "section": "Isolation",
            "path": "wiki/rag-design.md",
            "content": "Each tenant gets an isolated disposable RAG projection.",
            "score": 0.91,
        }]

    async def related(self, page, depth=1):
        return []


class FakeScopedVaults:
    def __init__(self, knowledge):
        self.knowledge = knowledge

    def for_scope(self, scope):
        return self.knowledge


async def main() -> None:
    workspace = Path(tempfile.mkdtemp(prefix="prepende_graphify_"))
    root = workspace / "vault"
    source = root / "wiki" / "rag-design.md"
    source.parent.mkdir(parents=True)
    source.write_text("# Vault RAG\n\nEach tenant has an isolated projection.\n", encoding="utf-8")

    # Tenant content exists in the same checkout but must never enter the owner
    # manifest, freshness inventory, or recall projection.
    tenant_source = root / "tenants" / "acme" / "wiki" / "secret.md"
    tenant_source.parent.mkdir(parents=True)
    tenant_source.write_text("Acme private operating fact\n", encoding="utf-8")

    out = workspace / "graphify-out"
    out.mkdir()
    cached_nodes = [
        {"id": "rag_vaultragindex", "label": "VaultRagIndex", "file_type": "document",
         "source_file": "vault/wiki/rag-design.md", "source_location": "1"},
        {"id": "scoped_scopedvaults", "label": "ScopedVaults", "file_type": "document",
         "source_file": "vault/wiki/rag-design.md", "source_location": "2"},
    ]
    cached_edges = [{
        "source": "scoped_scopedvaults", "target": "rag_vaultragindex",
        "relation": "constructs", "confidence": "EXTRACTED", "confidence_score": 1.0,
        "source_file": "vault/wiki/rag-design.md", "source_location": "3",
        "weight": 1.0,
    }]
    graph = {
        "directed": True,
        "multigraph": False,
        "graph": {},
        "nodes": [
            {**cached_nodes[0], "provenance": [{
                "source_file": "vault/wiki/rag-design.md", "source_location": "1",
            }]},
            {**cached_nodes[1], "provenance": [{
                "source_file": "vault/wiki/rag-design.md", "source_location": "2",
            }]},
        ],
        "links": [dict(cached_edges[0])],
        "hyperedges": [],
    }
    (out / "graph.json").write_text(json.dumps(graph), encoding="utf-8")
    cache_key = _graphify_cache_key(source, root.parent)
    cache_path = out / "cache" / "semantic" / f"{cache_key}.json"
    cache_path.parent.mkdir(parents=True)
    cache_path.write_text(json.dumps({
        "nodes": cached_nodes,
        "edges": cached_edges,
        "hyperedges": [],
    }), encoding="utf-8")
    detection = {
        "files": {"code": [str(source)], "document": [str(tenant_source)]},
        "total_files": 2,
        "graphifyignore_patterns": 2,
    }
    finalized = finalize_projection(
        root, detection, output_dir=out, mode="test", python_executable=sys.executable,
        graphify_version="0.6.1-test",
    )
    projection = GraphifyProjection(out / "graph.json", expected_root=root)
    status = projection.status()
    assert finalized["ok"] and status["ready"], (finalized, status)
    assert status["newFileCheck"] == "verified" and status["sourceFiles"] == 1, status
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    metadata = json.loads((out / "projection.json").read_text(encoding="utf-8"))
    assert all("vault/tenants" not in path for path in manifest), manifest
    assert metadata["scope"] == "owner" and metadata["files"] == ["wiki/rag-design.md"], metadata
    assert metadata["schemaVersion"] == 2 and metadata["buildId"] == status["buildId"], metadata
    assert metadata["sourceHashes"]["wiki/rag-design.md"] == sha256_file(source), metadata
    assert status["attestation"] == "graphify-content-addressed-cache", status
    print("OK finalized owner projection: exact source + graph + cache build identity sealed")

    notes = await projection.recall(
        "vault rag tenant isolation", source_hints=["wiki/rag-design.md"], k=1, neighbor_k=2
    )
    assert notes and notes[0]["source"] == "graphify", notes
    assert any(note.get("relation") == "constructs" for note in notes), notes
    recalled = await unified_recall(
        "vault rag tenant isolation", knowledge=FakeKnowledge(), graphify=projection, vault=True
    )
    assert recalled["sources"]["vault"] == 1, recalled
    assert recalled["sources"]["graphify"] >= 1, recalled
    print("OK bounded recall: current Graphify nodes/edges augment owner vault RAG")

    # Runtime tenant changes are deliberately outside the owner inventory and
    # must not make its projection stale (or leak tenant filenames into it).
    (tenant_source.parent / "new-private.md").write_text(
        "another Acme-only fact\n", encoding="utf-8"
    )
    assert projection.status()["ready"] is True, projection.status()
    print("OK ignored-root freshness: tenant runtime changes do not stale owner Graphify")

    # Product tenant loops may receive their own vault/RAG namespace, but never
    # the repository/corpus Graphify object attached to the owner loop.
    gateway = EchoGateway()
    old_loop = v1_api._loop
    try:
        v1_api._loop = SimpleNamespace(
            gateway=gateway,
            strategist=RulesStrategist(gateway),
            workspace=object(),
            memory=None,
            connectors=None,
            runs=None,
            knowledge=FakeKnowledge(),
            verifier=None,
            graphify=projection,
            scoped_vaults=FakeScopedVaults(FakeKnowledge()),
        )
        tenant_loop = v1_api._tenant_loop("tenant-a")
        assert tenant_loop.graphify is None, "owner Graphify entered a tenant loop"
    finally:
        v1_api._loop = old_loop
    print("OK owner boundary: hosted tenant loop receives no Graphify projection")

    # A sibling checkout can still exist and have matching source mtimes. The
    # explicit expected-root binding must reject it before recall.
    sibling = workspace / "vault-sibling"
    sibling.mkdir()
    (out / ".graphify_root").write_text(str(sibling), encoding="utf-8")
    wrong_root = projection.status()
    assert wrong_root["ready"] is False and wrong_root["reason"] == "root_mismatch", wrong_root
    (out / ".graphify_root").write_text(str(root), encoding="utf-8")
    assert projection.status()["ready"] is True
    print("OK clone/worktree binding: wrong-root graph is rejected")

    graph_path = out / "graph.json"
    projection_path = out / "projection.json"
    original_graph = graph_path.read_bytes()
    original_projection = projection_path.read_bytes()
    original_source = source.read_bytes()
    source_stat = source.stat()

    # Exact content hashes close the mtime hole: changing bytes while restoring
    # the old mtime must still make both recall and re-finalization fail closed.
    source.write_text("# Vault RAG\n\nStale graph must never be re-certified.\n", encoding="utf-8")
    os.utime(source, ns=(source_stat.st_atime_ns, source_stat.st_mtime_ns))
    hash_stale = GraphifyProjection(graph_path, expected_root=root).status()
    assert hash_stale["ready"] is False, hash_stale
    assert hash_stale["reason"] == "source_hash_mismatch", hash_stale
    assert await GraphifyProjection(graph_path, expected_root=root).recall("vault rag") == []
    try:
        finalize_projection(
            root, detection, output_dir=out, mode="adversarial-stale",
            python_executable=sys.executable, graphify_version="0.6.1-test",
        )
    except RuntimeError as exc:
        assert "cache" in str(exc).lower(), exc
    else:
        raise AssertionError("manifest-only finalization certified a stale graph")
    assert projection_path.read_bytes() == original_projection, (
        "failed finalization rewrote trust metadata"
    )
    source.write_bytes(original_source)
    os.utime(source, ns=(source_stat.st_atime_ns, source_stat.st_mtime_ns))
    assert GraphifyProjection(graph_path, expected_root=root).status()["ready"] is True
    print("OK content binding: same-mtime source tamper cannot be recalled or re-certified")

    # Graph bytes are also part of the build identity. A label-only mutation
    # with otherwise-valid provenance must be rejected independently.
    tampered = json.loads(original_graph)
    tampered["nodes"][0]["label"] = "Injected stale label"
    graph_path.write_text(json.dumps(tampered), encoding="utf-8")
    graph_stale = GraphifyProjection(graph_path, expected_root=root).status()
    assert graph_stale["ready"] is False, graph_stale
    assert graph_stale["reason"] == "graph_digest_mismatch", graph_stale
    assert await GraphifyProjection(graph_path, expected_root=root).recall("injected") == []
    graph_path.write_bytes(original_graph)
    assert GraphifyProjection(graph_path, expected_root=root).status()["ready"] is True
    print("OK artifact binding: graph-byte tamper invalidates the sealed build")

    def reseal_digest_only(mutated_graph: dict) -> None:
        """Model a forged digest so provenance must defend independently."""
        graph_path.write_text(json.dumps(mutated_graph), encoding="utf-8")
        forged = json.loads(original_projection)
        forged["graphSha256"] = sha256_file(graph_path)
        forged["buildId"] = projection_build_id(
            forged["corpusSha256"],
            forged["graphSha256"],
            forged["extractionEvidence"]["sha256"],
        )
        projection_path.write_text(json.dumps(forged), encoding="utf-8")

    provenance_attacks = []
    private_node = json.loads(original_graph)
    private_node["nodes"][0]["source_file"] = "vault/tenants/acme/wiki/secret.md"
    provenance_attacks.append(private_node)
    outside_edge = json.loads(original_graph)
    outside_edge["links"][0]["source_file"] = "/tmp/not-owner.md"
    provenance_attacks.append(outside_edge)
    unlisted_provenance = json.loads(original_graph)
    unlisted_provenance["nodes"][0]["provenance"] = [{
        "source_file": "vault/wiki/not-in-owner-manifest.md",
    }]
    provenance_attacks.append(unlisted_provenance)
    for attack in provenance_attacks:
        reseal_digest_only(attack)
        attacked = GraphifyProjection(graph_path, expected_root=root).status()
        assert attacked["ready"] is False, attacked
        assert attacked["reason"] == "graph_provenance_invalid", attacked
        assert attacked["invalidProvenance"] >= 1, attacked
    graph_path.write_bytes(original_graph)
    projection_path.write_bytes(original_projection)
    assert GraphifyProjection(graph_path, expected_root=root).status()["ready"] is True
    print("OK provenance allowlist: private, outside, and unlisted node/edge claims are rejected")

    # The writer enforces the same allowlist before replacing projection.json.
    malicious = json.loads(original_graph)
    malicious["links"][0]["source_file"] = "vault/tenants/acme/wiki/secret.md"
    graph_path.write_text(json.dumps(malicious), encoding="utf-8")
    try:
        finalize_projection(
            root, detection, output_dir=out, mode="adversarial-provenance",
            python_executable=sys.executable, graphify_version="0.6.1-test",
        )
    except RuntimeError as exc:
        assert "provenance" in str(exc).lower(), exc
    else:
        raise AssertionError("finalization accepted private edge provenance")
    assert projection_path.read_bytes() == original_projection, (
        "invalid graph replaced projection metadata"
    )
    graph_path.write_bytes(original_graph)
    assert GraphifyProjection(graph_path, expected_root=root).status()["ready"] is True
    print("OK writer boundary: finalization refuses malicious graph provenance atomically")

    # A new source has no old mtime to compare. Directory inventory closes that
    # hole and forces an explicit refresh before the graph can be recalled.
    (source.parent / "new_module.py").write_text("VALUE = 1\n", encoding="utf-8")
    new_file = projection.status()
    assert new_file["ready"] is False and new_file["reason"] == "source_set_changed", new_file
    assert await projection.recall("vault rag") == [], "new-file-stale graph entered recall"
    print("OK new-file freshness: source tree change is detected and recall fails closed")

    print("\nsmoke_graphify_recall: ALL OK")


if __name__ == "__main__":
    asyncio.run(main())
