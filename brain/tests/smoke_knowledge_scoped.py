"""Scoped-vault smoke — per-tenant knowledge namespaces stay isolated. Zero infra.
    python tests/smoke_knowledge_scoped.py

Covers the cross-tenant knowledge-exposure hole: the default scope keeps the
operator's vault; every other scope gets its own vault tree + RAG index under
<vault>/tenants/<scope>/, and one namespace can never read another (search,
pages, or link graph). Scope slugs are validated before touching the
filesystem, so hostile scopes cannot traverse out of tenants/.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from knowledge.scoped import (  # noqa: E402
    ScopedVaults,
    retrieval_scope_binding,
    tenant_vault_path,
    validate_scope,
)
from knowledge.vault import VaultKnowledge  # noqa: E402
from models.echo import EchoGateway  # noqa: E402
from prepende_brain.identity import namespace_for_identity  # noqa: E402


async def main() -> None:
    tmp = tempfile.mkdtemp(prefix="engram_scoped_kb_")
    # Hermetic: temp vault tests must never share the operator projection.
    os.environ["MEMORY_DB"] = os.path.join(tmp, "state", "memory.db")
    base = os.path.join(tmp, "vault")
    operator = VaultKnowledge(base, EchoGateway())
    vaults = ScopedVaults(base, default_scope="default", default_knowledge=operator,
                          gateway=EchoGateway())

    # Scope validation: slugs only, no traversal, no empties.
    assert validate_scope("tenant-a") == "tenant-a"
    for bad in ("", "  ", "../evil", "a/b", "UPPER", "a" * 65, ".", "..", "x y"):
        try:
            validate_scope(bad)
            raise AssertionError(f"scope {bad!r} should have been rejected")
        except ValueError:
            pass
    p = tenant_vault_path(base, "tenant-a")
    assert str(p).startswith(os.path.realpath(tmp)), p
    assert os.sep + "tenants" + os.sep in str(p), p

    # The default scope IS the operator vault (identity, not a copy).
    assert vaults.for_scope("default") is operator
    # Tenant scopes are cached per scope.
    a = vaults.for_scope("tenant-a")
    assert vaults.for_scope("tenant-a") is a

    # Writes land inside the tenant's own namespace, nowhere else.
    await a.write_page("private-plan", "# Private Plan\n\nLinks to [[pricing-notes]].\n")
    await a.write_page("pricing-notes", "# Pricing Notes\n\nReferenced by [[private-plan]].\n")
    a_page = os.path.join(base, "tenants", "tenant-a", "wiki", "private-plan.md")
    assert os.path.exists(a_page), a_page
    assert not os.path.exists(os.path.join(base, "wiki", "private-plan.md")), "leaked into operator wiki"

    # Ingest works per-namespace and updates the namespace's own index/log.
    pages = list(await a.ingest("Tenant A builds steel structures and tracks projects."))
    assert pages, "ingest produced no page"
    assert os.path.exists(os.path.join(base, "tenants", "tenant-a", "index.md"))

    # ISOLATION: tenant-b and the operator see none of tenant-a's knowledge.
    b = vaults.for_scope("tenant-b")
    assert list(await b.list_pages()) == [], "tenant-b vault should start empty"
    index_paths = {os.path.realpath(kb.rag.path) for kb in (operator, a, b)}
    assert len(index_paths) == 3, f"vault namespaces share a RAG index: {index_paths}"
    assert all(path.startswith(os.path.realpath(tmp)) for path in index_paths), index_paths
    b_hits = list(await b.search("private plan pricing"))
    assert b_hits == [], f"tenant-b must not see tenant-a chunks: {b_hits}"
    op_hits = list(await operator.search("private plan pricing"))
    assert all("private-plan" not in str(h) for h in op_hits), f"operator saw tenant page: {op_hits}"
    a_hits = list(await a.search("private plan pricing"))
    assert a_hits, "tenant-a should find its own knowledge"

    # Same page name and concurrent vector work must remain scope-isolated.
    await a.write_page("shared-name", "# Alpha Secret\n\nalpha-only-orbit belongs to tenant A.\n")
    await b.write_page("shared-name", "# Beta Secret\n\nbeta-only-quasar belongs to tenant B.\n")

    async def yielding_embedder(texts):
        await asyncio.sleep(0)
        return [[float("alpha" in text.lower()), float("beta" in text.lower()), 1.0]
                for text in texts]

    a.set_embedder(yielding_embedder, profile="test:scoped:3:v1")
    b.set_embedder(yielding_embedder, profile="test:scoped:3:v1")
    a_private, b_private = await asyncio.gather(
        a.search("alpha-only-orbit"), b.search("beta-only-quasar")
    )
    assert a_private and all("beta-only-quasar" not in h["content"] for h in a_private), a_private
    assert b_private and all("alpha-only-orbit" not in h["content"] for h in b_private), b_private

    # The wikilink graph is namespace-local: backlinks/related resolve inside
    # tenant-a and are invisible from tenant-b.
    assert "private-plan" in await a.backlinks("pricing-notes")
    assert "pricing-notes" in await a.related("private-plan")
    assert await b.related("private-plan") == []

    # Rich identities bind their canonical physical namespace without reducing
    # tenant and workspace to a caller-supplied scope label.
    rich_scope = namespace_for_identity("tenant-rich", "workspace-rich")
    rich = vaults.for_scope(
        rich_scope,
        tenant_id="tenant-rich",
        workspace_id="workspace-rich",
    )
    rich_binding = retrieval_scope_binding(rich)
    assert rich_binding is not None
    assert rich_binding.tenant_id == "tenant-rich", rich_binding
    assert rich_binding.workspace_id == "workspace-rich", rich_binding
    assert rich_binding.scope_id == rich_scope, rich_binding
    assert vaults.for_scope(
        rich_scope,
        tenant_id="tenant-rich",
        workspace_id="workspace-rich",
    ) is rich
    for kwargs in (
        {"tenant_id": "tenant-rich"},
        {"tenant_id": "other-tenant", "workspace_id": "workspace-rich"},
    ):
        try:
            vaults.for_scope(rich_scope, **kwargs)
        except ValueError:
            pass
        else:
            raise AssertionError(f"non-canonical rich identity was accepted: {kwargs}")
    # The configured owner corpus is a server-owned legacy binding: its
    # workspace may differ from its memory scope without turning the scope into
    # a guessed composite tenant id. Only that exact default identity is exempt
    # from the derived namespace rule.
    owner_root = os.path.join(tmp, "owner-default")
    owner_default = VaultKnowledge(owner_root, EchoGateway())
    owner_vaults = ScopedVaults(
        owner_root,
        default_scope="owner-scope",
        default_knowledge=owner_default,
        default_tenant_id="owner-scope",
        default_workspace_id="owner-workspace",
    )
    owner_binding = retrieval_scope_binding(owner_default)
    assert owner_binding is not None
    assert owner_binding.tenant_id == "owner-scope", owner_binding
    assert owner_binding.workspace_id == "owner-workspace", owner_binding
    assert owner_vaults.for_scope(
        "owner-scope",
        tenant_id="owner-scope",
        workspace_id="owner-workspace",
    ) is owner_default
    try:
        owner_vaults.for_scope(
            "owner-scope",
            tenant_id="different-tenant",
            workspace_id="owner-workspace",
        )
    except ValueError:
        pass
    else:
        raise AssertionError("default corpus accepted a different rich identity")

    print("SCOPED KNOWLEDGE SMOKE: OK")
    print(f"  isolated indexes: {len(index_paths)}")
    print(f"  tenant-a pages : {sorted(await a.list_pages())}")
    print(f"  tenant-b pages : {sorted(await b.list_pages())}")
    print(f"  operator pages : {sorted(await operator.list_pages())}")


if __name__ == "__main__":
    asyncio.run(main())
