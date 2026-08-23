"""ConnectorHub — the brain's outbound reach. Implements kernel.contracts.Connectors.

The hub registers connectors (n8n, Figma, and any MCP server) and
exposes a single way to discover and call their capabilities. Credentials live
in a gitignored .env and are Prepende's own keys, never another product's;
configuration never implies readiness: a connector reports `ready: true` only
with a fresh same-scope read-only probe receipt.
"""

from __future__ import annotations

import os
from typing import Any, Sequence

from connectors.readiness import ConnectorReadinessStore, DEFAULT_READINESS_TTL_SECONDS
from kernel.contracts import Connectors
from kernel.core.scope import ScopeIdentity


class ConnectorHub(Connectors):
    name = "hub"

    def __init__(self, readiness: ConnectorReadinessStore | None = None) -> None:
        self._adapters: dict[str, Any] = {}
        self.readiness = readiness or ConnectorReadinessStore()

    async def register(self, spec: Any) -> str:
        # spec is an adapter object with .name, .kind, optional .auth_env, .tools, async .call
        self._adapters[spec.name] = spec
        return spec.name

    def add(self, adapter: Any) -> None:
        """Sync registration, for composition at build time."""
        self._adapters[adapter.name] = adapter

    def _configured(self, adapter: Any) -> bool:
        configured = getattr(adapter, "configured", None)
        if callable(configured):
            return bool(configured())
        env = getattr(adapter, "auth_env", None)
        if getattr(adapter, "kind", None) == "local" or not env:
            return True
        return bool(os.environ.get(env, "").strip())

    @staticmethod
    def _capability(adapter: Any, tool: str) -> dict[str, Any]:
        declared = getattr(adapter, "tool_capabilities", {}) or {}
        capability = dict(declared.get(tool, {})) if isinstance(declared, dict) else {}
        capability.setdefault("supported", True)
        capability.setdefault("mode", "direct")
        capability.setdefault("approvalRequired", False)
        capability.setdefault("directCall", True)
        return capability

    @staticmethod
    def _identity(tenant_id: str | None, workspace_id: str | None) -> ScopeIdentity | None:
        if tenant_id is None and workspace_id is None:
            return None
        if tenant_id is None or workspace_id is None:
            raise ValueError("both tenant_id and workspace_id are required for connector readiness")
        return ScopeIdentity(tenant_id=tenant_id, workspace_id=workspace_id)

    async def list_tools(
        self,
        *,
        scope: str | None = None,
        tenant_id: str | None = None,
        workspace_id: str | None = None,
    ) -> Sequence[Any]:
        # ``scope`` remains a read-only compatibility parameter.  It is never
        # expanded into a workspace identity; only explicit two-part scope can
        # consume a verified receipt.
        identity = self._identity(tenant_id, workspace_id)
        tools: list[dict[str, Any]] = []
        for a in self._adapters.values():
            configured = self._configured(a)
            state = (
                self.readiness.state(identity, a.name, configured=configured)
                if identity is not None
                else {
                    "status": "configured" if configured else "unknown",
                    "authenticated": None,
                    "authentication": "not_checked",
                    "verified": False,
                    "operational": False,
                    "receipt": None,
                }
            )
            for t in getattr(a, "tools", []):
                capability = self._capability(a, t)
                supported = bool(capability["supported"])
                operational = supported and bool(state["operational"])
                direct_call = bool(capability["directCall"])
                tools.append({
                    "id": f"{a.name}.{t}",
                    "connector": a.name,
                    "tool": t,
                    "kind": getattr(a, "kind", "?"),
                    "configured": configured,
                    "authenticated": state["authenticated"],
                    "authentication": state["authentication"],
                    "verified": state["verified"],
                    "readiness": state["status"],
                    "readinessReceipt": state.get("receipt"),
                    "supported": supported,
                    "operational": operational,
                    "mode": capability["mode"],
                    "approvalRequired": bool(capability["approvalRequired"]),
                    "directCall": direct_call,
                    "reason": capability.get("reason", ""),
                    # ``ready`` remains the compatibility flag consumed by the
                    # model tool loop. Approval-gated or unsupported actions are
                    # never advertised as directly callable.
                    "ready": operational and direct_call,
                })
        return tools

    async def probe(
        self,
        connector: str,
        *,
        tenant_id: str,
        workspace_id: str,
        ttl_seconds: float = DEFAULT_READINESS_TTL_SECONDS,
    ) -> dict[str, Any]:
        identity = ScopeIdentity(tenant_id=tenant_id, workspace_id=workspace_id)
        adapter = self._adapters.get(connector)
        if adapter is None:
            raise ValueError(f"unknown connector: {connector}")
        configured = self._configured(adapter)
        version = str(getattr(adapter, "version", "1"))
        probe_type = str(getattr(adapter, "probe_type", "read_only"))
        if not configured:
            return self.readiness.record(
                identity, connector=connector, connector_version=version,
                probe_type=probe_type, ok=False, reason="connector is not configured",
                ttl_seconds=ttl_seconds,
            )
        probe = getattr(adapter, "probe", None)
        if not callable(probe) or getattr(adapter, "probe_read_only", False) is not True:
            return self.readiness.record(
                identity, connector=connector, connector_version=version,
                probe_type=probe_type, ok=False,
                reason="connector has no registered read-only readiness probe",
                ttl_seconds=ttl_seconds,
            )
        try:
            result = await probe()
        except Exception as exc:
            result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        if not isinstance(result, dict):
            result = {"ok": False, "error": "probe returned a non-object result"}
        if result.get("actionExecuted") is True or result.get("externalActions") not in (None, "none", []):
            result = {"ok": False, "error": "probe violated the non-mutating readiness contract"}
        evidence = {
            key: value for key, value in result.items()
            if key in {
                "status", "capabilities", "toolCount", "protocolVersion",
                "authenticated", "authentication", "operational",
            }
        }
        return self.readiness.record(
            identity, connector=connector, connector_version=version,
            probe_type=probe_type, ok=bool(result.get("ok")),
            reason=str(result.get("error") or result.get("reason") or "")[:500],
            evidence=evidence, ttl_seconds=ttl_seconds,
        )

    def readiness_state(
        self, connector: str, *, tenant_id: str, workspace_id: str
    ) -> dict[str, Any]:
        identity = ScopeIdentity(tenant_id=tenant_id, workspace_id=workspace_id)
        adapter = self._adapters.get(connector)
        if adapter is None:
            raise ValueError(f"unknown connector: {connector}")
        return self.readiness.state(identity, connector, configured=self._configured(adapter))

    def require_verified(
        self, connector: str, *, tenant_id: str, workspace_id: str
    ) -> dict[str, Any]:
        state = self.readiness_state(
            connector, tenant_id=tenant_id, workspace_id=workspace_id
        )
        if state["status"] != "verified":
            raise PermissionError(
                f"connector '{connector}' readiness is {state['status']}; "
                "a fresh same-scope read-only probe receipt is required"
            )
        return state

    async def call(
        self,
        tool_id: str,
        args: dict[str, Any],
        *,
        scope: str | None = None,
        tenant_id: str | None = None,
        workspace_id: str | None = None,
    ) -> Any:
        cname, _, tool = tool_id.partition(".")
        a = self._adapters.get(cname)
        if not a:
            return {"ok": False, "error": f"unknown connector: {cname}"}
        if tool not in getattr(a, "tools", []):
            return {"ok": False, "error": f"unknown connector tool: {tool_id}"}
        capability = self._capability(a, tool)
        if not capability["supported"]:
            return {
                "ok": False,
                "unsupported": True,
                "error": capability.get("reason") or f"connector tool '{tool_id}' is unsupported",
                "supported": False,
                "operational": False,
                "actionExecuted": False,
                "externalActions": "none",
            }
        if capability["approvalRequired"] and not capability["directCall"]:
            return {
                "ok": False,
                "error": (
                    f"connector tool '{tool_id}' requires Prepende approval staging; "
                    "direct connector execution is disabled"
                ),
                "approvalRequired": True,
                "operational": False,
                "actionExecuted": False,
                "externalActions": "none",
            }
        try:
            identity = self._identity(tenant_id, workspace_id)
        except ValueError as exc:
            return {"ok": False, "error": str(exc), "readiness": "unknown"}
        if identity is None:
            return {
                "ok": False,
                "error": "tenant_id and workspace_id are required for connector calls",
                "readiness": "unknown",
            }
        if not self._configured(a):
            return {"ok": False, "error": f"connector '{cname}' not configured — set {getattr(a, 'auth_env', '?')} in .env (Prepende's own key)", "readiness": "unknown"}
        try:
            state = self.require_verified(
                cname, tenant_id=identity.tenant_id, workspace_id=identity.workspace_id
            )
        except PermissionError as exc:
            return {
                "ok": False, "error": str(exc),
                "readiness": self.readiness_state(
                    cname, tenant_id=identity.tenant_id, workspace_id=identity.workspace_id
                )["status"],
            }
        if not bool(state.get("operational")):
            return {
                "ok": False,
                "error": (
                    f"connector '{cname}' is verified but '{tool}' is not operationally proven; "
                    "run its exact read-only capability probe"
                ),
                "readiness": state["status"],
                "verified": True,
                "operational": False,
            }
        result = await a.call(tool, args or {})
        if isinstance(result, dict):
            result.setdefault("readinessReceiptId", (state.get("receipt") or {}).get("id"))
        return result
