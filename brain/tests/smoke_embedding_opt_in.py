"""Embedding privacy smoke: generation credentials never imply vector export."""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import kernel.core.brain as brain_module  # noqa: E402
from kernel.core.config import Config  # noqa: E402


class GenerationOnlyGateway:
    name = "openai"
    model = "generation-only-test"

    def __init__(self) -> None:
        self.embed_calls = 0

    async def complete(self, messages, **opts):
        return "generation remains available"

    async def embed(self, texts, **opts):
        self.embed_calls += 1
        return [[float(len(text)), 0.0, 0.0, 0.0, 0.0, 0.0, 1.0]
                for text in texts]


async def exercise(loop, vault: Path) -> None:
    note = vault / "wiki" / "privacy-proof.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("# Privacy proof\n\nLexical recall stays local by default.\n", encoding="utf-8")
    await loop.memory.write("private memory stays local", scope="default")
    hits = await loop.knowledge.search("lexical recall local", k=3)
    assert hits and hits[0]["page"] == "privacy-proof", hits


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="prepende_embedding_opt_in_") as raw_tmp:
        tmp = Path(raw_tmp)
        cfg = Config()
        cfg.provider = "openai"
        cfg.model = "generation-only-test"
        cfg.embedding_provider = ""
        cfg.embedding_model = ""
        cfg.memory_backend = "sqlite"
        cfg.database_url = ""
        cfg.memory_scope = "default"
        cfg.workspace_scope = "default"
        cfg.memory_db = str(tmp / "memory.db")
        cfg.runs_db = str(tmp / "runs.db")
        cfg.self_improve_db = str(tmp / "self-improvement.db")
        cfg.connector_readiness_db = str(tmp / "connector-readiness.db")
        cfg.knowledge_db = str(tmp / "knowledge.db")
        cfg.workspace_root = str(tmp / "workspaces")
        cfg.vault = str(tmp / "vault")
        cfg.graphify_graph = str(tmp / "missing-graph.json")
        cfg.prompts_dir = str(tmp / "prompts")

        gateway = GenerationOnlyGateway()
        original = brain_module.build_gateway
        calls: list[tuple[str | None, str | None]] = []

        def fake_build_gateway(config, provider=None, model=None):
            calls.append((provider, model))
            return gateway

        try:
            brain_module.build_gateway = fake_build_gateway
            loop, _, returned = brain_module.build_brain(cfg, memory_policy="candidate")
            assert returned is gateway
            assert calls == [(None, None)], calls
            assert getattr(loop.memory, "_embedder", None) is None
            assert getattr(loop.knowledge.rag, "_embedder", None) is None
            asyncio.run(exercise(loop, Path(cfg.vault)))
            assert gateway.embed_calls == 0, gateway.embed_calls
            tenant = loop.scoped_vaults.for_scope("tenant-a")
            assert getattr(tenant.rag, "_embedder", None) is None

            # Once explicitly configured, the declared dimension is carried
            # through the owner composition root and every scoped vault.
            cfg.embedding_provider = "openai"
            cfg.embedding_model = "embedding-seven"
            cfg.embedding_dim = 7
            calls.clear()
            configured, _, _ = brain_module.build_brain(
                cfg, memory_policy="candidate"
            )
            assert calls == [(None, None), ("openai", "embedding-seven")], calls
            assert configured.knowledge.rag.expected_dimension == 7
            configured_tenant = configured.scoped_vaults.for_scope("tenant-b")
            assert configured_tenant.rag.expected_dimension == 7
        finally:
            brain_module.build_gateway = original

    print("EMBEDDING OPT-IN SMOKE: OK — generation works; private recall stays lexical")


if __name__ == "__main__":
    main()
