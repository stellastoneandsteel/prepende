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

from dataclasses import dataclass
import threading
import weakref
from pathlib import Path
from typing import Any

from knowledge.vault import VaultKnowledge
from prepende_brain.identity import (
    require_identity_namespace,
    validate_identity_slug,
)

# Same spirit as memory/postgres_store._check_scope: a scope is a short lowercase
# slug. Stricter here because the scope becomes a directory name.
from knowledge.paths import tenant_vault_path, validate_scope


@dataclass(frozen=True)
class RetrievalScopeBinding:
    """Server-owned logical identity for one resolved knowledge handle."""

    tenant_id: str
    workspace_id: str
    scope_id: str

    def as_dict(self) -> dict[str, str]:
        return {
            "tenantId": self.tenant_id,
            "workspaceId": self.workspace_id,
            "scopeId": self.scope_id,
        }


_BINDINGS_LOCK = threading.RLock()
_RETRIEVAL_BINDINGS: weakref.WeakKeyDictionary[Any, RetrievalScopeBinding] = (
    weakref.WeakKeyDictionary()
)


def bind_retrieval_scope(
    knowledge: Any,
    *,
    tenant_id: str,
    workspace_id: str,
    scope_id: str,
) -> RetrievalScopeBinding:
    """Bind a corpus handle once; rebinding it across scopes is forbidden."""

    if knowledge is None:
        raise ValueError("knowledge handle is required")
    binding = RetrievalScopeBinding(
        tenant_id=str(tenant_id or "").strip(),
        workspace_id=str(workspace_id or "").strip(),
        scope_id=validate_scope(scope_id),
    )
    if not binding.tenant_id or not binding.workspace_id:
        raise ValueError("tenant_id and workspace_id are required")
    with _BINDINGS_LOCK:
        previous = _RETRIEVAL_BINDINGS.get(knowledge)
        if previous is not None and previous != binding:
            raise ValueError("knowledge handle is already bound to another retrieval scope")
        _RETRIEVAL_BINDINGS[knowledge] = binding
    return binding


def retrieval_scope_binding(knowledge: Any) -> RetrievalScopeBinding | None:
    """Read the resolver-owned binding without trusting hit or request metadata."""

    with _BINDINGS_LOCK:
        try:
            return _RETRIEVAL_BINDINGS.get(knowledge)
        except TypeError:
            # Third-party immutable/value adapters may not support weak refs.
            # They can still expose a complete server-owned identity directly.
            return None




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
        *,
        default_tenant_id: str | None = None,
        default_workspace_id: str | None = None,
    ) -> None:
        self.base = Path(base_path)
        self.default_scope = validate_scope(default_scope)
        self.default = default_knowledge
        self.gateway = gateway
        self._cache: dict[str, VaultKnowledge] = {}
        self.default_tenant_id = validate_identity_slug(
            default_tenant_id or self.default_scope,
            "default_tenant_id",
        )
        self.default_workspace_id = validate_identity_slug(
            default_workspace_id or self.default_scope,
            "default_workspace_id",
        )
        # The configured owner/default corpus predates derived physical
        # namespaces. Its tenant label is the existing memory scope and its
        # workspace label may legitimately differ, so this exact composition-
        # root binding is an explicit server-owned legacy exception. Rich
        # authenticated tenant namespaces are still canonicalized below.
        if self.default is not None:
            bind_retrieval_scope(
                self.default,
                tenant_id=self.default_tenant_id,
                workspace_id=self.default_workspace_id,
                scope_id=self.default_scope,
            )

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

    def for_scope(
        self,
        scope: str,
        *,
        tenant_id: str | None = None,
        workspace_id: str | None = None,
    ) -> Any:
        """Return one physical namespace with an immutable logical binding.

        Legacy callers may keep passing only a scope. Authenticated surfaces
        with distinct tenant/workspace identities must pass both; their
        canonical physical namespace is checked before a handle is returned.
        """

        s = validate_scope(scope)
        if (tenant_id is None) != (workspace_id is None):
            raise ValueError("tenant_id and workspace_id must be supplied together")
        if tenant_id is not None and workspace_id is not None:
            logical_tenant = validate_identity_slug(tenant_id, "tenant_id")
            logical_workspace = validate_identity_slug(workspace_id, "workspace_id")
            default_binding = (
                s == self.default_scope
                and logical_tenant == self.default_tenant_id
                and logical_workspace == self.default_workspace_id
            )
            if not default_binding:
                require_identity_namespace(logical_tenant, logical_workspace, s)
        elif s == self.default_scope:
            logical_tenant = self.default_tenant_id
            logical_workspace = self.default_workspace_id
        else:
            logical_tenant = s
            logical_workspace = s
        if s == self.default_scope and self.default is not None:
            bind_retrieval_scope(
                self.default,
                tenant_id=logical_tenant,
                workspace_id=logical_workspace,
                scope_id=s,
            )
            return self.default
        if s in self._cache:
            cached = self._cache[s]
            bind_retrieval_scope(
                cached,
                tenant_id=logical_tenant,
                workspace_id=logical_workspace,
                scope_id=s,
            )
            return cached
        kb = VaultKnowledge(str(tenant_vault_path(self.base, s)), self.gateway)
        embedder = self._shared_embedder()
        if embedder is not None:
            kb.set_embedder(
                embedder,
                profile=self._shared_embedding_profile(),
                expected_dimension=self._shared_expected_dimension(),
            )
        bind_retrieval_scope(
            kb,
            tenant_id=logical_tenant,
            workspace_id=logical_workspace,
            scope_id=s,
        )
        self._cache[s] = kb
        return kb
