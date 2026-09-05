"""Pure namespace resolution shared by retrieval and model-free status."""
from __future__ import annotations
import hashlib
import re
from pathlib import Path

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
    slug = validate_scope(scope)
    tenants = Path(base).expanduser() / "tenants"
    requested = tenants / slug
    # Containment alone permits a sibling-scope symlink. Never let a caller's
    # logical scope be relabeled as a different physical corpus.
    if tenants.is_symlink() or requested.is_symlink():
        raise ValueError("tenant vault namespace must not be a symlink")
    root = requested.resolve()
    tenants_root = tenants.resolve()
    if root.parent != tenants_root or root.name != slug:
        raise ValueError("scope escapes the tenants directory: %r" % scope)
    return root


def vault_index_path(
    vault: Path, *, memory_db: Path, configured_vault: Path, override: str = ""
) -> str:
    """Resolve the runtime and read-only status index with the same namespace rule."""
    state_dir = memory_db.expanduser().resolve().parent
    resolved_vault = vault.expanduser().resolve()
    configured_vault = configured_vault.expanduser().resolve()
    if resolved_vault == configured_vault:
        return str(Path(override).expanduser()) if override else str(state_dir / "vault_index.db")
    digest = hashlib.sha256(str(resolved_vault).encode("utf-8")).hexdigest()[:16]
    return str(state_dir / "vault_indexes" / f"{digest}.db")
