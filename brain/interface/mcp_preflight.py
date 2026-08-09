"""Dependency-free MCP launch identity preflight.

This validates the exact environment that will be handed to the MCP server but
does not import the MCP package, build a brain, open a port, or call a model.
"""

from __future__ import annotations

import json
import os
import sys

from interface.mcp_scope import (
    allowed_capabilities,
    deployment_revision,
    startup_scope_guard,
)
from prepende_brain.identity import require_identity_namespace, validate_identity_slug
from prepende_brain.env import brand_env


def _env(suffix: str) -> str:
    return brand_env(f"MCP_{suffix}")


def receipt() -> dict[str, object]:
    problem = startup_scope_guard()
    if problem:
        raise ValueError(problem)
    tenant = _env("TENANT")
    workspace = _env("WORKSPACE")
    scope = _env("SCOPE")
    if tenant or workspace:
        if not tenant or not workspace:
            raise ValueError("MCP tenant and workspace must be configured together")
        scope = require_identity_namespace(tenant, workspace, scope)
    else:
        scope = validate_identity_slug(
            scope or os.environ.get("MEMORY_SCOPE") or "default", "scope"
        )
        tenant = workspace = scope
    revision = deployment_revision()
    return {
        "ok": True,
        "transport": _env("TRANSPORT") or "stdio",
        "tenant": tenant,
        "workspace": workspace,
        "scope": scope,
        "deploymentRevision": revision or "unconfigured",
        "deploymentRevisionConfigured": revision is not None,
        "capabilities": sorted(allowed_capabilities()),
        "externalActions": "approval_required",
        "started": False,
        "preflightOnly": True,
    }


def main() -> int:
    try:
        value = receipt()
    except ValueError as exc:
        print("Prepende MCP startup refused: " + str(exc), file=sys.stderr)
        return 2
    print(json.dumps(value, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
