"""Thin stdlib adapters for Prepende's outbound connector hub.

n8n execution is deliberately unavailable as a direct tool call: registered
workflows must pass through Prepende's approval ledger. Figma exposes the
official read-only REST ``GET /v1/files/:key`` transport; the PAT-based adapter
does not pretend that Figma's separate remote-MCP write tools are available.

Credentials live only in private environment configuration (SEPARATION.md).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import urllib.error
import urllib.request
from urllib.parse import quote, urlencode, urlsplit, urlunsplit
from typing import Any


_FIGMA_API_BASE = "https://api.figma.com"
_FIGMA_RESPONSE_LIMIT = 2_000_000
_FIGMA_FILE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")


class N8nAdapter:
    """Readiness for the generic n8n lane; execution stays approval-gated."""
    name = "n8n"
    kind = "http"
    auth_env = "N8N_WEBHOOK_URL"
    tools = ["run_workflow"]
    version = "2"
    probe_read_only = True
    probe_type = "http_get_healthz_readiness"
    tool_capabilities = {
        "run_workflow": {
            "supported": True,
            "mode": "approval_gated",
            "approvalRequired": True,
            "directCall": False,
        }
    }

    def configured(self) -> bool:
        return bool(os.environ.get("N8N_WEBHOOK_URL", "").strip())

    async def probe(self) -> dict[str, Any]:
        """GET n8n's readiness health endpoint; never touch a webhook path."""
        raw = os.environ.get("N8N_WEBHOOK_URL", "").strip()
        parts = urlsplit(raw)
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            return {"ok": False, "error": "N8N_WEBHOOK_URL is not a valid HTTP(S) URL"}
        url = urlunsplit((parts.scheme, parts.netloc, "/healthz/readiness", "", ""))

        def _get() -> int:
            req = urllib.request.Request(url, method="GET", headers={"accept": "application/json,text/plain"})
            with urllib.request.urlopen(req, timeout=10) as response:
                response.read(1000)
                return int(response.status)

        try:
            status = await asyncio.to_thread(_get)
            return {
                "ok": 200 <= status < 300,
                "status": status,
                # The health endpoint proves reachability/readiness only. It
                # does not authenticate or execute the configured webhook.
                "authenticated": None,
                "authentication": "not_checked",
                "operational": False,
                "capabilities": {"run_workflow": "approval_gated_unverified"},
                "externalActions": "none",
                "actionExecuted": False,
            }
        except Exception as exc:
            return {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "authenticated": None,
                "authentication": "not_checked",
                "operational": False,
                "externalActions": "none",
                "actionExecuted": False,
            }

    async def call(self, tool: str, args: dict[str, Any]) -> Any:
        return {
            "ok": False,
            "connector": "n8n",
            "tool": tool,
            "approvalRequired": True,
            "actionExecuted": False,
            "externalActions": "none",
            "error": (
                "n8n workflow execution is not a direct connector call; stage a registered "
                "workflow through Prepende's approval lane"
            ),
        }


class FigmaAdapter:
    """Official Figma REST read transport with explicit write non-support."""
    name = "figma"
    kind = "http"
    auth_env = "FIGMA_API_KEY"
    tools = ["get_design", "create_design"]
    version = "2"
    probe_read_only = True
    probe_type = "figma_get_me_and_optional_file"
    tool_capabilities = {
        "get_design": {
            "supported": True,
            "mode": "read_only",
            "approvalRequired": False,
            "directCall": True,
        },
        "create_design": {
            "supported": False,
            "mode": "unsupported",
            "approvalRequired": False,
            "directCall": False,
            "reason": (
                "unsupported by Prepende's PAT-based REST adapter; Figma canvas writes "
                "are a separate remote-MCP capability for supported clients"
            ),
        },
    }

    def configured(self) -> bool:
        return bool(os.environ.get("FIGMA_API_KEY", "").strip())

    @staticmethod
    def _file_key(value: Any) -> str:
        raw = str(value or "").strip()
        if raw.startswith(("http://", "https://")):
            parts = [part for part in urlsplit(raw).path.split("/") if part]
            raw = parts[1] if len(parts) >= 2 and parts[0] in {"design", "file", "board"} else ""
        if not raw or not _FIGMA_FILE_KEY.fullmatch(raw):
            raise ValueError("file_key must be a Figma file key or Figma file URL")
        return raw

    @staticmethod
    def _request(
        path: str,
        *,
        query: dict[str, str] | None = None,
        limit: int = _FIGMA_RESPONSE_LIMIT,
    ) -> tuple[int, dict[str, Any]]:
        key = os.environ.get("FIGMA_API_KEY", "").strip()
        if not key:
            raise ValueError("FIGMA_API_KEY is not configured")
        url = f"{_FIGMA_API_BASE}{path}"
        if query:
            url = f"{url}?{urlencode(query)}"
        req = urllib.request.Request(
            url,
            method="GET",
            headers={"X-Figma-Token": key, "accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=15) as response:
            body = response.read(limit + 1)
            if len(body) > limit:
                raise ValueError(f"Figma response exceeded the {limit}-byte safety limit")
            parsed = json.loads(body.decode() or "{}")
            if not isinstance(parsed, dict):
                raise ValueError("Figma returned a non-object JSON response")
            return int(response.status), parsed

    async def probe(self) -> dict[str, Any]:
        if not self.configured():
            return {"ok": False, "error": "FIGMA_API_KEY is not configured"}
        authenticated = False
        try:
            status, _ = await asyncio.to_thread(self._request, "/v1/me", limit=20_000)
            authenticated = 200 <= status < 300
            probe_file = os.environ.get("FIGMA_FILE_KEY", "").strip()
            operational = False
            capabilities = {
                "get_design": "implemented_needs_file_probe",
                "create_design": "unsupported",
            }
            if probe_file:
                file_key = self._file_key(probe_file)
                file_status, _ = await asyncio.to_thread(
                    self._request,
                    f"/v1/files/{quote(file_key, safe='')}",
                    query={"depth": "1"},
                )
                status = file_status
                operational = 200 <= file_status < 300
                capabilities["get_design"] = "verified_read_only" if operational else "probe_failed"
            return {
                "ok": 200 <= status < 300,
                "status": status,
                "authenticated": True,
                "authentication": "verified",
                "operational": operational,
                "capabilities": capabilities,
                "externalActions": "none",
                "actionExecuted": False,
            }
        except urllib.error.HTTPError as exc:
            return {
                "ok": False,
                "error": f"Figma API returned HTTP {exc.code}",
                # A file-level 403/404 after /v1/me succeeded is a scope or
                # file-access failure, not a failed token authentication.
                "authenticated": True if authenticated else (False if exc.code in {401, 403} else None),
                "authentication": "verified" if authenticated else ("failed" if exc.code in {401, 403} else "unknown"),
                "operational": False,
                "externalActions": "none",
                "actionExecuted": False,
            }
        except Exception as exc:
            return {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "authenticated": None,
                "authentication": "unknown",
                "operational": False,
                "externalActions": "none",
                "actionExecuted": False,
            }

    async def call(self, tool: str, args: dict[str, Any]) -> Any:
        if tool == "create_design":
            return {
                "ok": False,
                "unsupported": True,
                "connector": "figma",
                "tool": tool,
                "actionExecuted": False,
                "externalActions": "none",
                "error": self.tool_capabilities[tool]["reason"],
            }
        if tool != "get_design":
            return {"ok": False, "error": f"unknown Figma tool: {tool}"}
        try:
            file_key = self._file_key(
                args.get("file_key")
                or args.get("fileKey")
                or args.get("url")
                or os.environ.get("FIGMA_FILE_KEY", "")
            )
            depth = int(args.get("depth", 2))
            if depth < 1 or depth > 10:
                raise ValueError("depth must be between 1 and 10")
            query = {"depth": str(depth)}
            ids = args.get("ids")
            if isinstance(ids, list):
                ids = ",".join(str(item).strip() for item in ids if str(item).strip())
            if ids:
                query["ids"] = str(ids)[:2000]
            if args.get("version"):
                query["version"] = str(args["version"])[:200]
            if args.get("geometry") == "paths":
                query["geometry"] = "paths"
            if args.get("branch_data") is True or args.get("branchData") is True:
                query["branch_data"] = "true"
            status, design = await asyncio.to_thread(
                self._request,
                f"/v1/files/{quote(file_key, safe='')}",
                query=query,
            )
            return {
                "ok": 200 <= status < 300,
                "connector": "figma",
                "tool": tool,
                "status": status,
                "mode": "read_only",
                "actionExecuted": False,
                "externalActions": "none",
                "design": design,
            }
        except urllib.error.HTTPError as exc:
            return {
                "ok": False,
                "connector": "figma",
                "tool": tool,
                "status": exc.code,
                "actionExecuted": False,
                "externalActions": "none",
                "error": f"Figma API returned HTTP {exc.code}",
            }
        except Exception as exc:
            return {
                "ok": False,
                "connector": "figma",
                "tool": tool,
                "actionExecuted": False,
                "externalActions": "none",
                "error": f"{type(exc).__name__}: {exc}",
            }


class NewsAdapter:
    """Real headlines over public RSS — the brain's own research reach.

    Registered 2026-08-06 (founder: brain-written articles must be able to
    gather their own sources; connectors/news.py existed but was never
    registered with the hub, so unaided goals wrote news from training
    memory — the mechanism behind the day's retracted articles).

    No credential: the feed registry is public RSS, overridable per tenant
    with NEWS_FEEDS. Fetching is read-only GET; nothing here mutates any
    external system, so the tool is direct-call without approval.
    """

    name = "news"
    kind = "rss"
    auth_env = ""
    tools = ["fetch_headlines"]
    version = "1"
    probe_read_only = True
    probe_type = "rss_fetch_read_only"
    tool_capabilities = {
        "fetch_headlines": {
            "supported": True,
            "mode": "read_only",
            "approvalRequired": False,
            "directCall": True,
        }
    }

    def configured(self) -> bool:
        """Public feeds ship in the registry; NEWS_FEEDS only overrides them."""
        return True

    async def probe(self) -> dict[str, Any]:
        """Fetch a small page of headlines; read-only by construction."""
        from connectors import news

        def _fetch() -> list[dict]:
            return news.fetch_headlines(limit=3, timeout=8)

        try:
            items = await asyncio.to_thread(_fetch)
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        if not items:
            return {"ok": False, "error": "no feed returned any headline"}
        return {
            "ok": True,
            "operational": True,
            "status": "headlines_available",
            "toolCount": len(self.tools),
            "actionExecuted": False,
            "externalActions": "none",
        }

    async def call(self, tool: str, args: dict[str, Any]) -> Any:
        if tool != "fetch_headlines":
            return {"ok": False, "error": f"unknown news tool: {tool}"}
        from connectors import news

        try:
            limit = max(1, min(40, int(args.get("limit") or 12)))
        except (TypeError, ValueError):
            limit = 12
        max_age = args.get("max_age_hours")
        try:
            max_age = max(1, min(24 * 14, int(max_age))) if max_age is not None else None
        except (TypeError, ValueError):
            max_age = None
        query = str(args.get("query") or "").strip().lower()

        def _fetch() -> list[dict]:
            # Over-fetch when filtering so a narrow query still fills the page.
            return news.fetch_headlines(limit=limit * 3 if query else limit, timeout=10, max_age_hours=max_age)

        try:
            items = await asyncio.to_thread(_fetch)
        except Exception as exc:
            return {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "actionExecuted": False,
                "externalActions": "none",
            }
        if query:
            terms = [t for t in query.split() if len(t) >= 3]
            items = [
                it for it in items
                if all(t in f"{it.get('title','')} {it.get('summary','')}".lower() for t in terms)
            ] or [
                it for it in items
                if any(t in f"{it.get('title','')} {it.get('summary','')}".lower() for t in terms)
            ]
        return {
            "ok": True,
            "connector": "news",
            "tool": tool,
            "mode": "read_only",
            "count": len(items[:limit]),
            "headlines": items[:limit],
            "actionExecuted": False,
            "externalActions": "none",
        }
