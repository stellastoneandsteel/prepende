"""Workflow selector — the brain discovers available n8n workflows and picks one.

You register your n8n workflows (name, description, webhook URL) in
workflows.json (gitignored — holds URLs/keys) or the PREPENDE_WORKFLOWS env var
(legacy ENGRAM_WORKFLOWS remains accepted).
Prepende can then:
  - list them (the menu),
  - SELECT the best-matching workflow for a goal (LLM match, deterministic
    keyword fallback), and
  - execute it only after a matching one-time approval receipt, using the n8n
    connector's auth.

This is the "workflow selector" — deterministic automation the brain hands work
off to ("workflows beat agents" for the boring, repeatable stuff). Each workflow
entry:
    {"name": str, "description": str, "url": str, "params": {..optional defaults..}}

Stdlib only. Best-effort: a missing config = empty menu, never an error.
"""

from __future__ import annotations

import asyncio
import json
import os
import urllib.request
from urllib.parse import urlsplit
from pathlib import Path
from typing import Any

from prepende_brain.env import brand_env


def load_workflows(path: str = "./workflows.json") -> list[dict]:
    raw = brand_env("WORKFLOWS").strip()
    data: Any = None
    if raw:
        try:
            data = json.loads(raw)
        except Exception:
            return []
    else:
        p = Path(path)
        if not p.exists():
            return []
        try:
            data = json.loads(p.read_text())
        except Exception:
            return []
    wfs = data.get("workflows", data) if isinstance(data, dict) else data
    return [w for w in wfs if isinstance(w, dict) and w.get("name")] if isinstance(wfs, list) else []


class WorkflowSelector:
    def __init__(
        self, gateway: Any = None, path: str = "./workflows.json", *, connectors: Any = None
    ) -> None:
        self.gateway = gateway
        self.path = path
        self.connectors = connectors

    def list(self) -> list[dict]:
        """The menu: name + description only (no URLs leaked to callers)."""
        return [{"name": w["name"], "description": w.get("description", "")} for w in load_workflows(self.path)]

    def _get(self, name: str) -> dict | None:
        for w in load_workflows(self.path):
            if w["name"] == name:
                return w
        return None

    async def select(self, goal: str) -> str | None:
        """Pick the best-matching workflow for a goal. LLM if available, else keywords."""
        wfs = load_workflows(self.path)
        if not wfs:
            return None
        if len(wfs) == 1:
            return wfs[0]["name"]
        if self.gateway is not None:
            menu = "\n".join(f"- {w['name']}: {w.get('description','')}" for w in wfs)
            out = await self.gateway.complete([{"role": "user", "content":
                f"Goal: {goal}\n\nWhich ONE of these workflows best fits? Reply with the exact name only, "
                f"or 'none' if none fit.\n{menu}"}], max_tokens=20)
            pick = (out or "").strip().split()[0].strip(".,:") if out else ""
            for w in wfs:
                if w["name"].lower() == pick.lower():
                    return w["name"]
        # deterministic fallback: keyword overlap of goal vs name+description
        terms = [t for t in goal.lower().split() if len(t) > 2]
        best, score = None, 0
        for w in wfs:
            hay = (w["name"] + " " + w.get("description", "")).lower()
            s = sum(t in hay for t in terms)
            if s > score:
                best, score = w["name"], s
        return best

    async def run(
        self,
        name: str,
        params: dict[str, Any] | None = None,
        *,
        tenant_id: str | None = None,
        workspace_id: str | None = None,
        approval_id: str | None = None,
    ) -> dict:
        w = self._get(name)
        if not w:
            return {"ok": False, "error": f"unknown workflow: {name}"}
        url = w.get("url", "").strip()
        if not url:
            return {"ok": False, "error": f"workflow '{name}' has no webhook URL configured"}
        params = params or {}
        if (
            not approval_id
            or params.get("approvalId") != approval_id
            or params.get("mode") != "live"
            or params.get("requiresApproval") is not False
        ):
            return {
                "ok": False,
                "error": "workflow execution requires a matching approved Prepende receipt",
                "approvalRequired": True,
                "actionExecuted": False,
                "externalActions": "none",
            }
        if self.connectors is None:
            return {
                "ok": False,
                "error": "connector readiness is unavailable; workflow execution is disabled",
                "actionExecuted": False,
                "externalActions": "none",
            }
        readiness_url = os.environ.get("N8N_WEBHOOK_URL", "").strip()
        readiness_origin = urlsplit(readiness_url)
        workflow_origin = urlsplit(url)
        if (
            readiness_origin.scheme not in {"http", "https"}
            or not readiness_origin.netloc
            or (readiness_origin.scheme, readiness_origin.netloc)
            != (workflow_origin.scheme, workflow_origin.netloc)
        ):
            return {
                "ok": False,
                "error": "registered workflow origin does not match the verified n8n origin",
                "actionExecuted": False,
                "externalActions": "none",
            }
        readiness_receipt_id = None
        if not tenant_id or not workspace_id:
            return {
                "ok": False,
                "error": "tenant_id and workspace_id are required for connector-backed workflows",
                "readiness": "unknown",
            }
        try:
            readiness = self.connectors.require_verified(
                "n8n", tenant_id=tenant_id, workspace_id=workspace_id
            )
        except (PermissionError, ValueError) as exc:
            state = self.connectors.readiness_state(
                "n8n", tenant_id=tenant_id, workspace_id=workspace_id
            )
            return {"ok": False, "error": str(exc), "readiness": state["status"]}
        readiness_receipt_id = (readiness.get("receipt") or {}).get("id")
        payload = {**(w.get("params") or {}), **params}

        def _post() -> str:
            body = json.dumps(payload).encode()
            headers = {"content-type": "application/json"}
            key = os.environ.get("N8N_API_KEY", "").strip()
            if key:
                headers["authorization"] = f"Bearer {key}"
            req = urllib.request.Request(url, data=body, headers=headers)
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read().decode()[:4000]

        try:
            return {
                "ok": True, "workflow": name, "response": await asyncio.to_thread(_post),
                "readiness": "verified", "readinessReceiptId": readiness_receipt_id,
            }
        except Exception as exc:
            return {"ok": False, "workflow": name, "error": f"{type(exc).__name__}: {exc}"}
