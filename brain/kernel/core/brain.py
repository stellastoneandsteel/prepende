"""build_brain — assemble the kernel from config. One place wires the ports.

This is the composition root: pick the model adapter, the workspace, the
strategist, and hand them to the Goal Loop. Every surface (REPL, TUI, later a
remote API) calls this and then drives `loop.run`.
"""

from __future__ import annotations

from pathlib import Path

from connectors.defaults import default_hub
from kernel.core.config import Config
from kernel.core.loop import GoalLoop
from kernel.core.runs import RunStore
from kernel.core.strategist import RulesStrategist
from knowledge.scoped import ScopedVaults
from knowledge.vault import VaultKnowledge
from memory.factory import build_memory
from models.factory import build_gateway
from prompts.registry import FilePromptRegistry
from self_improve.improver import SelfImprover
from self_improve.store import SelfImprovementStore
from workspace.local import LocalWorkspace


def _configured_embedder(gateway, embedding_model: str = ""):
    """Return an embed callable that carries the configured embedding model."""
    model = (embedding_model or "").strip()
    if not model:
        return gateway.embed

    async def embed(texts):
        return await gateway.embed(texts, model=model)

    return embed


def _embedding_profile(cfg: Config, gateway) -> str:
    """Stable identity of the vector space used by rebuildable projections."""
    provider = cfg.embedding_provider or cfg.provider or getattr(gateway, "name", "unknown")
    model = cfg.embedding_model or cfg.model or getattr(gateway, "model", "default") or "default"
    return f"{provider}:{model}:{cfg.embedding_dim}:v1"


def build_brain(cfg: Config | None = None, memory_policy: str | None = None):
    cfg = cfg or Config()
    gateway = build_gateway(cfg)
    # Embeddings are selected INDEPENDENTLY of generation (kernel/contracts/model.py).
    # They are also explicit opt-in: a generation key must never silently export
    # private memory/vault text to that provider's embedding endpoint. When
    # EMBEDDING_PROVIDER is blank, both stores remain lexical-only.
    embed_gateway = None
    embedder = None
    if cfg.embedding_provider:
        embed_gateway = build_gateway(cfg, provider=cfg.embedding_provider, model=cfg.embedding_model or None)
        embedder = _configured_embedder(embed_gateway, cfg.embedding_model)
    workspace = LocalWorkspace(cfg.workspace_root)
    memory = build_memory(cfg)  # postgres (Supabase) if configured, else sqlite
    # Hybrid recall: stores that support it embed through the independent embedder.
    if embedder is not None and hasattr(memory, "set_embedder"):
        memory.set_embedder(embedder)
    connectors = default_hub(cfg.connector_readiness_db)
    runs = RunStore(cfg.runs_db)
    knowledge = VaultKnowledge(cfg.vault, gateway)
    # The vault's RAG projection embeds through the same independent embedder.
    if embedder is not None and embed_gateway is not None:
        knowledge.set_embedder(
            embedder,
            profile=_embedding_profile(cfg, embed_gateway),
            expected_dimension=cfg.embedding_dim,
        )
    # Graphify is optional, read-only, and owner-only. Missing or stale output
    # must leave ordinary memory + vault recall intact.
    graphify = None
    if cfg.graphify_graph:
        from knowledge.graphify import GraphifyProjection
        graph_path = Path(cfg.graphify_graph).expanduser().resolve()
        graphify = GraphifyProjection(str(graph_path), expected_root=cfg.vault)
    strategist = RulesStrategist(gateway)
    # Heavy tactics get a fixed, adversarial three-lens verifier panel by
    # default. ENGRAM_VERIFY=1 extends it to solo; an explicit false value turns
    # it off. GoalLoop applies the tactic gate and records every skip/failure.
    from kernel.core.verifier import ResultVerifier, verification_mode
    verify_mode = verification_mode()
    verifier = (
        None
        if verify_mode == "off"
        else ResultVerifier(gateway, mode=verify_mode)
    )
    # Memory stays Assess-gated ("candidate") unless a surface that truly needs
    # automatic durable writes (the TUI) opts into "auto" explicitly.
    # vault_recall: the composition root builds the OWNER brain, so unified
    # recall may read its wiki (RAG hits + one-hop link-graph walk). Hosted
    # surfaces resolve separate tenant vaults below and never receive Graphify.
    # Default policy "diary" (autonomy, Balanced): the OWNER brain keeps an
    # episodic autobiography automatically; semantic facts stay Assess-gated.
    # Product surfaces build their own tenant loops with an explicit
    # memory_policy="candidate", so tenants are unchanged by this default.
    loop = GoalLoop(gateway, strategist, workspace, memory=memory, scope=cfg.memory_scope,
                    workspace_id=cfg.workspace_scope,
                    connectors=connectors, runs=runs, knowledge=knowledge, verifier=verifier,
                    memory_policy=memory_policy or "diary", vault_recall=True,
                    graphify=graphify)
    # One resolver owns tenant vault construction across every product surface.
    # Each non-default scope receives a separate markdown tree and RAG database;
    # Graphify remains attached only to the owner loop above.
    loop.scoped_vaults = ScopedVaults(
        cfg.vault,
        default_scope=cfg.memory_scope,
        default_knowledge=knowledge,
        gateway=gateway,
    )
    # Self-improvement: versioned prompts + the gated propose->evaluate->commit loop.
    loop.prompts = FilePromptRegistry(cfg.prompts_dir)
    loop.improver = SelfImprover(
        gateway, loop.prompts, store=SelfImprovementStore(cfg.self_improve_db)
    )
    # Workflow selector: the brain discovers + picks the right n8n workflow for a goal.
    from connectors.workflows import WorkflowSelector
    loop.workflows = WorkflowSelector(gateway, connectors=connectors)
    # Knowledge-gathering scout layer: research/verify -> pending_review -> human accept.
    from knowledge.items import KnowledgeItemStore
    from agents.scout import KnowledgeScout
    loop.knowledge_items = KnowledgeItemStore(cfg.knowledge_db)
    loop.scout = KnowledgeScout(gateway, loop.knowledge_items, memory=memory,
                                knowledge=knowledge, scope=cfg.memory_scope)
    # Orchestration registry: one honest catalog of what the brain can orchestrate
    # (tactics, workflows, agents). Descriptive metadata only — the Strategist does
    # NOT consume it yet, so Goal Loop routing behavior is unchanged. Connector
    # tools are seeded on demand via seed_connector_tools (async).
    from kernel.core.registry import InMemoryRegistry, seed_registry
    loop.registry = seed_registry(InMemoryRegistry(), gateway=gateway, workflows=loop.workflows)
    return loop, cfg, gateway
