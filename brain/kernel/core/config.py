"""Config — reads .env (no dependency) and exposes settings.

Secrets live in a gitignored .env, never in the repo (see SEPARATION.md).
Default provider is `echo` so Engram runs out of the box with no key and no
cost; set MODEL_PROVIDER + a key in .env to go live.
"""

from __future__ import annotations

import os
from pathlib import Path


def mirror_brand_env() -> None:
    """Rename bridge (Wave 2): the substrate is being renamed Engram -> Prepende. To let
    the hosts migrate env var names without a flag day, mirror both prefixes -- a value
    set under either PREPENDE_* or ENGRAM_* fills in the other (setdefault, so it never
    overwrites an explicit value). Every existing os.environ.get("ENGRAM_X") read and any
    new PREPENDE_X name both resolve. Safe to run repeatedly."""
    for key, val in list(os.environ.items()):
        if key.startswith("ENGRAM_"):
            os.environ.setdefault("PREPENDE_" + key[len("ENGRAM_"):], val)
        elif key.startswith("PREPENDE_"):
            os.environ.setdefault("ENGRAM_" + key[len("PREPENDE_"):], val)


def load_dotenv(path: str = ".env") -> None:
    p = Path(path)
    if p.exists():
        for raw in p.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            if " #" in val:  # strip trailing inline comment
                val = val.split(" #", 1)[0]
            val = val.strip().strip('"').strip("'")
            # .env fills in anything not explicitly set. An EMPTY or missing ambient value
            # is treated as unset, so a stray empty var (e.g. an empty ANTHROPIC_API_KEY
            # some tools export) can't shadow the real .env value. A NON-EMPTY exported var
            # is a deliberate override and is respected — so don't export another product's
            # keys in Engram's shell (SEPARATION.md); .env is the intended source.
            if not os.environ.get(key):
                os.environ[key] = val
    # Always mirror the brand prefixes, even with no .env (host ambient vars).
    mirror_brand_env()


class Config:
    def __init__(self) -> None:
        load_dotenv()
        g = os.environ.get
        self.provider = (g("MODEL_PROVIDER", "echo") or "echo").strip()
        self.model = (g("MODEL_NAME", "") or "").strip()
        # MODEL_PROVIDER=auto is an explicit owner opt-in to cross-provider
        # generation routing. Each provider keeps its own model ID and key.
        self.model_route = (
            g("PREPENDE_MODEL_ROUTE", "")
            or g("ENGRAM_MODEL_ROUTE", "")
            or "anthropic,openai,grok,google"
        ).strip()
        self.anthropic_model = (
            g("PREPENDE_ANTHROPIC_MODEL", "")
            or g("ENGRAM_ANTHROPIC_MODEL", "")
            or ""
        ).strip()
        self.openai_model = (
            g("PREPENDE_OPENAI_MODEL", "")
            or g("ENGRAM_OPENAI_MODEL", "")
            or ""
        ).strip()
        self.grok_model = (
            g("PREPENDE_GROK_MODEL", "")
            or g("ENGRAM_GROK_MODEL", "")
            or "grok-2-latest"
        ).strip()
        self.google_model = (
            g("PREPENDE_GOOGLE_MODEL", "")
            or g("ENGRAM_GOOGLE_MODEL", "")
            or ""
        ).strip()
        # Embeddings are selected INDEPENDENTLY of generation (see kernel/contracts/model.py).
        # EMBEDDING_PROVIDER picks the embedder adapter, so e.g. MODEL_PROVIDER=anthropic can
        # generate while EMBEDDING_PROVIDER=openai (or a local model) produces the vectors.
        # Unset -> lexical-only recall. Generation credentials never implicitly
        # authorize sending private memory or vault text to an embedding endpoint.
        # Explicit providers remain interchangeable across openai/local adapters.
        self.embedding_provider = (g("EMBEDDING_PROVIDER", "") or "").strip()
        self.embedding_model = (g("EMBEDDING_MODEL", "") or g("ENGRAM_EMBEDDING_MODEL", "") or "").strip()
        # Vector dimension of the chosen embedder (OpenAI text-embedding-3-small=1536,
        # nomic-embed-text=768). The Postgres/Supabase pgvector column reads this so the
        # store is not hardcoded to one embedder. Default 1536 leaves prod unchanged.
        self.embedding_dim = int((g("EMBEDDING_DIM", "") or g("ENGRAM_EMBEDDING_DIMENSIONS", "") or "1536") or 1536)
        self.anthropic_key = (g("ANTHROPIC_API_KEY", "") or "").strip()
        self.openai_key = (g("OPENAI_API_KEY", "") or "").strip()
        self.google_key = (g("GOOGLE_API_KEY", "") or "").strip()
        self.xai_key = (g("XAI_API_KEY", "") or "").strip()  # Grok (xAI), OpenAI-compatible
        self.openai_compat_base = (g("OPENAI_COMPATIBLE_BASE_URL", "") or "").strip()
        self.openai_compat_key = (g("OPENAI_COMPATIBLE_API_KEY", "") or "").strip()
        self.local_base = (g("LOCAL_BASE_URL", "http://localhost:11434/v1") or "").strip()
        # BYO-brain key-vault master key (host env ONLY; never repo/DB). Presence
        # gates the encrypted per-tenant secret lane — see kernel/core/keyvault.py.
        self.vault_master_key = (g("ENGRAM_KEY_VAULT_MASTER_KEY", "") or "").strip()
        self.workspace_root = (g("WORKSPACE_ROOT", "./.workspaces") or "./.workspaces").strip()
        self.max_tokens = int(g("MAX_TOKENS_PER_RUN", "200000") or 200000)
        self.max_retries = int(g("MAX_RETRIES", "3") or 3)
        # Memory: zero-infra sqlite by default; Postgres (Supabase) is the production swap.
        self.memory_db = (g("MEMORY_DB", "./.engram/memory.db") or "./.engram/memory.db").strip()
        self.memory_scope = (g("MEMORY_SCOPE", "default") or "default").strip()
        # Explicit workspace identity for safety ledgers.  Existing single-workspace
        # deployments intentionally use the memory scope as their configured value.
        self.workspace_scope = (g("WORKSPACE_SCOPE", self.memory_scope) or self.memory_scope).strip()
        # MEMORY_BACKEND: auto | sqlite | postgres. "auto" -> postgres if DATABASE_URL is
        # a postgres URL, else sqlite. DATABASE_URL is the Supabase connection string.
        self.memory_backend = (g("MEMORY_BACKEND", "auto") or "auto").strip().lower()
        self.database_url = (g("DATABASE_URL", "") or "").strip()
        # Durable run journal: goals survive a crash and can resume.
        self.runs_db = (g("RUNS_DB", "./.engram/runs.db") or "./.engram/runs.db").strip()
        self.self_improve_db = (
            g("SELF_IMPROVE_DB", "./.engram/self_improvement.db")
            or "./.engram/self_improvement.db"
        ).strip()
        self.connector_readiness_db = (
            g("CONNECTOR_READINESS_DB", "./.engram/connector_readiness.db")
            or "./.engram/connector_readiness.db"
        ).strip()
        # Knowledge vault (the self-organizing wiki; source of truth, git-tracked).
        self.vault = (g("VAULT_PATH", "./vault") or "./vault").strip()
        # Optional Graphify read projection. Prepende never runs paid extraction
        # from the composition root; when graph.json exists the owner brain may
        # recall audited nodes/edges. Tenant loops never receive it.
        self.graphify_graph = (
            g("GRAPHIFY_GRAPH_PATH", "./graphify-out/graph.json")
            or "./graphify-out/graph.json"
        ).strip()
        # Versioned prompt store (what the self-improvement loop edits).
        self.prompts_dir = (g("PROMPTS_DIR", "./prompts/store") or "./prompts/store").strip()
        # Knowledge-gathering items (provenance + review states; the scout layer).
        self.knowledge_db = (g("KNOWLEDGE_DB", "./.engram/knowledge.db") or "./.engram/knowledge.db").strip()
