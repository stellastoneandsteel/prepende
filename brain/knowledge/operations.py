"""Operator-facing knowledge maintenance for Prepende.

The markdown vault is canonical.  RAG and Graphify are disposable projections,
so this module exposes explicit, receipt-producing operations instead of hiding
index work inside a chat request.  It intentionally builds only the configured
embedding lane; generation credentials are not required to inspect or rebuild
knowledge.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from kernel.core.brain import _configured_embedder, _embedding_profile
from kernel.core.config import Config
from knowledge.graphify import GraphifyProjection
from knowledge.vault import VaultKnowledge
from models.factory import build_gateway


def build_knowledge(cfg: Config | None = None) -> tuple[VaultKnowledge, Config, dict[str, Any]]:
    """Build the vault plus only an explicitly configured embedding gateway."""
    cfg = cfg or Config()
    knowledge = VaultKnowledge(cfg.vault)
    embedding: dict[str, Any] = {
        "configured": False,
        "provider": cfg.embedding_provider,
        "model": cfg.embedding_model,
        "dimension": cfg.embedding_dim,
        "profile": "",
    }
    if cfg.embedding_provider:
        gateway = build_gateway(
            cfg,
            provider=cfg.embedding_provider,
            model=cfg.embedding_model or None,
        )
        profile = _embedding_profile(cfg, gateway)
        change = knowledge.set_embedder(
            _configured_embedder(gateway, cfg.embedding_model),
            profile=profile,
            expected_dimension=cfg.embedding_dim,
        )
        embedding.update({"configured": True, "profile": profile, "change": change})
    return knowledge, cfg, embedding


def status(cfg: Config | None = None) -> dict[str, Any]:
    knowledge, cfg, embedding = build_knowledge(cfg)
    rag = knowledge.rag.status()
    graph = GraphifyProjection(cfg.graphify_graph, expected_root=cfg.vault).status()
    return {
        "ok": bool(rag["lexical_ready"]),
        "vault": {
            "source_files": rag["source_files"],
            "wiki_pages": len(list(knowledge.wiki.glob("*.md"))),
            "raw_pages": len(list((knowledge.root / "raw").glob("*.md"))),
            "obsidian_viewer_configured": (knowledge.root / ".obsidian").is_dir(),
        },
        "rag": rag,
        "embedding": embedding,
        "graphify": graph,
    }


async def rebuild(cfg: Config | None = None) -> dict[str, Any]:
    knowledge, cfg, embedding = build_knowledge(cfg)
    receipt = await knowledge.rag.rebuild()
    result = status(cfg)
    result.update({"operation": "rebuild", "receipt": receipt})
    # A lexical rebuild is a valid zero-key outcome.  When embeddings were
    # explicitly configured, incomplete vectors are a truthful failure.
    result["ok"] = bool(result["rag"]["lexical_ready"]) and (
        not embedding["configured"] or bool(result["rag"]["semantic_ready"])
    )
    return result


async def backfill(cfg: Config | None = None, *, max_rounds: int = 100) -> dict[str, Any]:
    knowledge, cfg, embedding = build_knowledge(cfg)
    receipt = await knowledge.rag.backfill_all(max_rounds=max_rounds)
    result = status(cfg)
    result.update({"operation": "backfill", "receipt": receipt})
    result["ok"] = bool(receipt["complete"])
    if not embedding["configured"]:
        result["ok"] = False
    return result


async def search(query: str, cfg: Config | None = None, *, k: int = 8) -> dict[str, Any]:
    knowledge, cfg, _ = build_knowledge(cfg)
    refresh = await knowledge.rag.refresh()
    hits = list(await knowledge.rag.search(query, k=max(1, min(int(k), 25))))
    return {
        "ok": True,
        "operation": "search",
        "query": query,
        "count": len(hits),
        "hits": hits,
        "refresh": refresh,
        "rag": knowledge.rag.status(),
    }


def print_receipt(value: dict[str, Any], *, as_json: bool = False) -> None:
    if as_json:
        print(json.dumps(value, indent=2, default=str))
        return
    rag = value.get("rag", {})
    graph = value.get("graphify", {})
    print(
        "Prepende knowledge: "
        f"{rag.get('source_files', 0)} files / {rag.get('chunks', 0)} chunks / "
        f"{rag.get('embedded_chunks', 0)} embedded"
    )
    print(
        "  RAG: "
        f"lexical={'ready' if rag.get('lexical_ready') else 'not ready'}, "
        f"semantic={'ready' if rag.get('semantic_ready') else 'not ready'}"
    )
    if graph:
        print(
            "  Graphify: "
            f"{'ready' if graph.get('ready') else 'not ready'} "
            f"({graph.get('reason', 'unknown')})"
        )
    if value.get("operation") == "search":
        for hit in value.get("hits", []):
            print(f"  - {hit.get('path')} :: {str(hit.get('content', ''))[:160]}")


def resolved_paths(cfg: Config | None = None) -> dict[str, str]:
    """Small helper for bootstrap/reporting without printing secrets."""
    knowledge, cfg, _ = build_knowledge(cfg)
    return {
        "vault": str(Path(cfg.vault).expanduser().resolve()),
        "rag": str(Path(knowledge.rag.path).expanduser().resolve()),
        "graphify": str(Path(cfg.graphify_graph).expanduser().resolve()),
    }
