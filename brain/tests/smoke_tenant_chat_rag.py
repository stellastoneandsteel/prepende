"""Tenant chat receives its own vault RAG, never another tenant or Graphify."""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class CaptureGateway:
    name = "capture"

    async def complete(self, messages, **opts):
        return str(messages[0]["content"])


async def main() -> None:
    tmp = tempfile.mkdtemp(prefix="prepende_tenant_chat_rag_")
    os.environ.update({
        "MODEL_PROVIDER": "echo",
        "EMBEDDING_PROVIDER": "echo",
        "MEMORY_BACKEND": "sqlite",
        "MEMORY_DB": os.path.join(tmp, "state", "memory.db"),
        "RUNS_DB": os.path.join(tmp, "state", "runs.db"),
        "SELF_IMPROVE_DB": os.path.join(tmp, "state", "self-improve.db"),
        "CONNECTOR_READINESS_DB": os.path.join(tmp, "state", "connectors.db"),
        "KNOWLEDGE_DB": os.path.join(tmp, "state", "knowledge.db"),
        "WORKSPACE_ROOT": os.path.join(tmp, "workspaces"),
        "VAULT_PATH": os.path.join(tmp, "vault"),
        "VAULT_INDEX_PATH": os.path.join(tmp, "state", "owner-rag.db"),
        "MEMORY_SCOPE": "operator",
        "WORKSPACE_SCOPE": "operator",
        "GRAPHIFY_GRAPH_PATH": os.path.join(tmp, "graphify", "graph.json"),
    })

    from interface import engram_api, prepende_runtime as v1_api

    # Reset module composition roots in case another smoke imported them first.
    v1_api._loop = v1_api._cfg = v1_api._gw = None
    engram_api._loop = engram_api._cfg = None

    owner = v1_api._brain()
    await owner.knowledge.write_page(
        "owner-secret", "# Owner Secret\n\noperator-only-nebula must stay private.\n"
    )
    tenant_a = v1_api._tenant_knowledge("tenant-a")
    tenant_b = v1_api._tenant_knowledge("tenant-b")
    await tenant_a.write_page(
        "launch-fact", "# Launch Fact\n\nalpha-orbit ships every Thursday.\n"
    )
    await tenant_b.write_page(
        "launch-fact", "# Launch Fact\n\nbeta-quasar ships every Monday.\n"
    )

    loop_a = v1_api._tenant_loop("tenant-a")
    assert loop_a.knowledge is tenant_a
    assert loop_a.vault_recall is True
    assert loop_a.graphify is None, "tenant loop inherited owner Graphify"

    a_hits = await tenant_a.search("alpha orbit Thursday")
    assert a_hits and "alpha-orbit" in a_hits[0]["content"], a_hits
    assert all("beta-quasar" not in h["content"] for h in a_hits), a_hits
    assert all("operator-only-nebula" not in h["content"] for h in a_hits), a_hits

    # The fast path folds the same scoped RAG context into its guarded system
    # block. A capture gateway lets the smoke inspect that block without a model.
    fast = await v1_api._fast_chat(
        "tenant-a", "when does alpha orbit ship?", [], gateway=CaptureGateway()
    )
    assert fast["knowledgeHitCount"] >= 1, fast
    assert "alpha-orbit" in fast["text"], fast["text"]
    assert "beta-quasar" not in fast["text"], fast["text"]
    assert "operator-only-nebula" not in fast["text"], fast["text"]

    # The generic /api/engram surface uses the same namespace contract.
    engram_api._loop = owner
    generic = engram_api._tenant_loop("tenant-a")
    assert generic.knowledge is tenant_a
    assert generic.vault_recall is True and generic.graphify is None

    print("TENANT CHAT RAG SMOKE: OK")
    print(f"  tenant-a knowledge hits: {fast['knowledgeHitCount']}")
    print("  owner / tenant-b content: excluded")
    print("  Graphify: owner-only")


if __name__ == "__main__":
    asyncio.run(main())
