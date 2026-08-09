"""McpConnector — Prepende as an MCP CLIENT, so it can call ANY MCP server itself.

This is the outbound half of "open both ways": Prepende reaches out to external
MCP servers (Figma, n8n, or anything MCP-compliant) on its own,
over the standard protocol. Hand-rolled JSON-RPC 2.0 over HTTP using stdlib
only (no `mcp` SDK needed for the client), so it works anywhere Python does.

Flow per server: initialize -> notifications/initialized -> tools/list, then
tools/call(name, args) on demand. Handles both JSON and SSE (text/event-stream)
responses and the Mcp-Session-Id header.

Registered into the ConnectorHub from config (see connectors/mcp_config.py), so
adding a server is config, not code. Each remote tool appears as
`<server>.<tool>` in the hub and is callable from a goal.
"""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from typing import Any

_PROTOCOL = "2025-06-18"


def _parse_response(raw: bytes, content_type: str) -> dict:
    """MCP servers reply with application/json OR text/event-stream (SSE)."""
    text = raw.decode("utf-8", "replace").strip()
    if "text/event-stream" in (content_type or ""):
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                payload = line[5:].strip()
                if payload and payload != "[DONE]":
                    try:
                        return json.loads(payload)
                    except Exception:
                        continue
        return {}
    return json.loads(text) if text else {}


class McpConnector:
    """One MCP server, exposed to the hub as a connector. kind='mcp'."""

    kind = "mcp"
    auth_env = None  # auth is the per-server token in config, not a single env var
    version = _PROTOCOL
    probe_read_only = True
    probe_type = "mcp_initialize_tools_list"

    def __init__(self, name: str, url: str, token: str = "", timeout: int = 60) -> None:
        self.name = name
        self.url = url
        self.token = token
        self.timeout = timeout
        self.tools: list[str] = []          # filled by connect()
        self._session: str | None = None
        self._id = 0
        self._connected = False

    # --- transport ---
    def _headers(self) -> dict[str, str]:
        h = {
            "content-type": "application/json",
            "accept": "application/json, text/event-stream",
        }
        if self.token:
            h["authorization"] = f"Bearer {self.token}"
        if self._session:
            h["mcp-session-id"] = self._session
        return h

    def _rpc(self, method: str, params: dict | None = None, *, notify: bool = False) -> dict:
        self._id += 1
        body = {"jsonrpc": "2.0", "method": method}
        if not notify:
            body["id"] = self._id
        if params is not None:
            body["params"] = params
        req = urllib.request.Request(self.url, data=json.dumps(body).encode(), headers=self._headers())
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            sid = resp.headers.get("mcp-session-id")
            if sid:
                self._session = sid
            if notify:
                return {}
            return _parse_response(resp.read(), resp.headers.get("content-type", ""))

    # --- lifecycle ---
    def connect(self) -> list[str]:
        """initialize -> initialized -> tools/list. Returns tool names."""
        self._rpc("initialize", {
            "protocolVersion": _PROTOCOL,
            "capabilities": {},
            "clientInfo": {"name": "engram", "version": "0.0.0"},
        })
        try:
            self._rpc("notifications/initialized", {}, notify=True)
        except Exception:
            pass  # some servers don't require/accept the notification
        listed = self._rpc("tools/list", {})
        tools = (listed.get("result") or {}).get("tools", [])
        self.tools = [t["name"] for t in tools if "name" in t]
        self._connected = True
        return self.tools

    def _call_sync(self, tool: str, args: dict[str, Any]) -> Any:
        """Blocking connect-if-needed + tools/call. Runs off the event loop (see call())."""
        if not self._connected:
            try:
                self.connect()
            except Exception as exc:
                return {"ok": False, "error": f"could not connect to MCP server '{self.name}': {exc}"}
        try:
            out = self._rpc("tools/call", {"name": tool, "arguments": args or {}})
        except urllib.error.HTTPError as e:
            return {"ok": False, "error": f"HTTP {e.code}: {e.read().decode('utf-8','replace')[:300]}"}
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        if "error" in out:
            return {"ok": False, "error": out["error"]}
        return {"ok": True, "connector": self.name, "tool": tool, "result": out.get("result")}

    # --- hub interface (async, matches the stub adapters) ---
    async def call(self, tool: str, args: dict[str, Any]) -> Any:
        # The transport is blocking stdlib urllib. Run it in a worker thread (as
        # N8nAdapter does) so the event loop stays responsive and caller-side
        # timeouts can regain control. Cancellation does not terminate that worker;
        # urllib's timeout remains the transport backstop, and a one-shot process
        # can still wait for a lingering worker during executor shutdown.
        return await asyncio.to_thread(self._call_sync, tool, args)

    def configured(self) -> bool:
        return bool(self.url.strip())

    async def probe(self) -> dict[str, Any]:
        try:
            tools = await asyncio.to_thread(self.connect)
            return {
                "ok": True,
                "toolCount": len(tools),
                "protocolVersion": _PROTOCOL,
                "externalActions": "none",
                "actionExecuted": False,
            }
        except Exception as exc:
            return {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "externalActions": "none",
                "actionExecuted": False,
            }
