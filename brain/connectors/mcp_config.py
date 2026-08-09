"""Load MCP servers from config and register them in the hub — config, not code.

Prepende learns about external MCP servers (Figma, n8n, or another verified
provider) from a
JSON file (default: ./mcp_servers.json, gitignored — it holds tokens) OR the
PREPENDE_MCP_SERVERS env var (legacy Engram alias accepted). Each entry:

    {"name": "design-tools", "url": "https://.../mcp", "token": "..."}

On startup the hub connects to each (best-effort) and exposes its remote tools
as `<name>.<tool>`. A server that's unreachable/misconfigured is skipped with a
note — it never blocks Prepende from starting. Tokens are Prepende's OWN, in a
gitignored file, never in the repo (SEPARATION.md).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from connectors.mcp_connector import McpConnector
from prepende_brain.env import brand_env


def load_mcp_servers(path: str = "./mcp_servers.json") -> list[dict]:
    raw = brand_env("MCP_SERVERS").strip()
    if raw:
        try:
            return json.loads(raw)
        except Exception:
            return []
    p = Path(path)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text())
    except Exception:
        return []
    return data.get("servers", data) if isinstance(data, (dict, list)) else []


def register_mcp_servers(hub, path: str = "./mcp_servers.json") -> list[str]:
    """Register configured MCP servers into the hub. Returns names registered."""
    names: list[str] = []
    for entry in load_mcp_servers(path):
        name, url = entry.get("name"), entry.get("url")
        if not name or not url:
            continue
        conn = McpConnector(name, url, entry.get("token", ""))
        try:
            conn.connect()  # best-effort: discover tools now
        except Exception:
            pass  # keep it registered; it'll report the error on call()
        hub.add(conn)
        names.append(name)
    return names
