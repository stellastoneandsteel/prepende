"""ScopedVaults — per-tenant vault namespaces over VaultKnowledge.

The memory layer is already scope-isolated (forced RLS + the app.engram_scope
GUC); the vault was not: one shared tree meant knowledge_search over MCP could
expose every tenant's knowledge to any connected agent. This closes that hole
with the same shape the memory layer uses — a scope slug picks the namespace,
nothing in the request body can widen it.

Layout:
    <base>/                      the operator's vault (the default scope; unchanged)
    <base>/tenants/<scope>/      one full vault per tenant scope: its own wiki/,
                                 raw/, index.md, log.md, AND its own RAG index
                                 (VaultKnowledge builds a per-vault VaultRagIndex,
                                 so tenant search never touches operator chunks)

Scope slugs are validated against the same character discipline as the memory
store (lowercase slug, no path separators) BEFORE touching the filesystem, so a
hostile scope can never traverse out of the tenants/ tree. The wikilink graph
(link_graph / backlinks / related) comes along for free: each namespace's graph
is computed over its own pages only.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from knowledge.vault import VaultKnowledge

# Same spirit as memory/postgres_store._check_scope: a scope is a short lowercase
# slug. Stricter here because the scope becomes a directory name.
_SCOPE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def validate_scope(scope: str) -> str:
    s = str(scope or "").strip()
    if not _SCOPE_RE.fullmatch(s):
        raise ValueError(
            "invalid vault scope %r: must be a lowercase slug ([a-z0-9_-], 1-64 chars)" % scope
        )
    return s


def tenant_vault_path(base: str | Path, scope: str) -> Path:
    """The vault root for a validated tenant scope, always under <base>/tenants/."""
    root = (Path(base) / "tenants" / validate_scope(scope)).resolve()
    tenants_root = (Path(base) / "tenants").resolve()
    if tenants_root not in root.parents:
        raise ValueError("scope escapes the tenants directory: %r" % scope)
    return root


class ScopedVaults:
    """Resolve a tenant scope to its own VaultKnowledge (cached per scope).

    The default scope maps to the operator's existing vault instance so current
    behaviour is unchanged; every other scope gets a namespaced vault with its
    own RAG index, sharing the operator's gateway and embedder (hybrid search
    per tenant, degrading to lexical exactly like the operator vault when no
    embedder is wired).
    """

    def __init__(
        self,
        base_path: str | Path,
        default_scope: str = "default",
        default_knowledge: Any = None,
        gateway: Any = None,
    ) -> None:
        self.base = Path(base_path)
        self.default_scope = validate_scope(default_scope)
        self.default = default_knowledge
        self.gateway = gateway
        self._cache: dict[str, VaultKnowledge] = {}

    def _shared_embedder(self) -> Any:
        rag = getattr(self.default, "rag", None)
        return getattr(rag, "_embedder", None)

    def _shared_embedding_profile(self) -> str:
        rag = getattr(self.default, "rag", None)
        return str(getattr(rag, "embedding_profile", "") or "")

    def _shared_expected_dimension(self) -> int | None:
        rag = getattr(self.default, "rag", None)
        value = getattr(rag, "expected_dimension", None)
        return int(value) if value is not None else None

    def for_scope(self, scope: str) -> Any:
        s = validate_scope(scope)
        if s == self.default_scope and self.default is not None:
            return self.default
        if s in self._cache:
            return self._cache[s]
        kb = VaultKnowledge(str(tenant_vault_path(self.base, s)), self.gateway)
        embedder = self._shared_embedder()
        if embedder is not None:
            kb.set_embedder(
                embedder,
                profile=self._shared_embedding_profile(),
                expected_dimension=self._shared_expected_dimension(),
            )
        self._cache[s] = kb
        return kb
