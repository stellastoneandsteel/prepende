"""Canonical tenant/workspace to physical-namespace binding.

The physical namespace is not caller-chosen for a rich identity.  It is a
deterministic function of both the commercial tenant and workspace, so two
tenants that happen to use the same workspace slug cannot share a vault or
memory scope.  Scope-only legacy identities remain self-scoped.
"""

from __future__ import annotations

import hashlib
import re


IDENTITY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def validate_identity_slug(value: object, label: str = "identity") -> str:
    slug = str(value or "").strip()
    if not IDENTITY_RE.fullmatch(slug):
        raise ValueError(
            f"{label} must be a lowercase slug [a-z0-9_-], 1-64 characters"
        )
    return slug


def namespace_for_identity(tenant: object, workspace: object) -> str:
    """Return the only physical scope allowed for ``tenant`` + ``workspace``.

    A one-part legacy identity remains readable (``acme`` -> ``acme``).  A
    distinct workspace is visibly bound to its tenant when it fits, otherwise
    a readable prefix plus a deterministic digest stays inside the 64-character
    scope limit.
    """

    tenant_slug = validate_identity_slug(tenant, "tenant")
    workspace_slug = validate_identity_slug(workspace, "workspace")
    if tenant_slug == workspace_slug:
        return tenant_slug
    readable = f"{tenant_slug}--{workspace_slug}"
    if len(readable) <= 64:
        return readable
    digest = hashlib.sha256(
        f"{tenant_slug}\0{workspace_slug}".encode("utf-8")
    ).hexdigest()[:20]
    return f"{tenant_slug[:18]}--{workspace_slug[:18]}--{digest}"


def require_identity_namespace(
    tenant: object, workspace: object, scope: object = ""
) -> str:
    """Derive the namespace or reject an explicitly mismatched physical scope."""

    expected = namespace_for_identity(tenant, workspace)
    supplied = str(scope or "").strip()
    if supplied:
        supplied = validate_identity_slug(supplied, "scope")
        if supplied != expected:
            raise ValueError(
                "physical scope does not match the canonical tenant/workspace namespace"
            )
    return expected
