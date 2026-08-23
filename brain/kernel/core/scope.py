"""Immutable tenant + workspace identity for scoped kernel artifacts.

Prepende historically used one ``scope`` string for tenant memory.  New safety
ledgers need both identities explicitly so a record cannot be reused merely
because two callers happen to share a tenant-level namespace.
"""

from __future__ import annotations

from dataclasses import dataclass
import re


_SCOPE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


@dataclass(frozen=True)
class ScopeIdentity:
    tenant_id: str
    workspace_id: str

    def __post_init__(self) -> None:
        for label, value in (("tenant_id", self.tenant_id), ("workspace_id", self.workspace_id)):
            if not isinstance(value, str) or not _SCOPE_ID.fullmatch(value.strip()):
                raise ValueError(
                    f"{label} is required and must match {_SCOPE_ID.pattern}"
                )
            object.__setattr__(self, label, value.strip())

    @property
    def prompt_scope(self) -> str:
        """Stable compound key for per-workspace prompt activation pointers."""
        return f"tenant:{self.tenant_id}|workspace:{self.workspace_id}"

    def as_dict(self) -> dict[str, str]:
        return {"tenantId": self.tenant_id, "workspaceId": self.workspace_id}

