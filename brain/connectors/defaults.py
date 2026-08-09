"""default_hub — the connector hub Engram boots with.

Registers the current kernel connectors: n8n (workflows) and Figma (design).
Image generation belongs to the active operator/cockpit, not a default kernel
connector. Future media providers can register behind the generic connector
contract after their exact image/video APIs are verified.
"""

from __future__ import annotations

from connectors.adapters import FigmaAdapter, N8nAdapter, NewsAdapter
from connectors.hub import ConnectorHub
from connectors.readiness import ConnectorReadinessStore
from connectors.mcp_config import register_mcp_servers


def default_hub(readiness_path: str = "./.engram/connector_readiness.db") -> ConnectorHub:
    hub = ConnectorHub(ConnectorReadinessStore(readiness_path))
    hub.add(N8nAdapter())
    hub.add(FigmaAdapter())
    # Public-RSS research reach: no credential, read-only, direct-call. Gives
    # goal runs real headlines instead of training-memory news (2026-08-06).
    hub.add(NewsAdapter())
    # Live MCP servers (Figma/n8n/anything) from mcp_servers.json or
    # ENGRAM_MCP_SERVERS — Engram as an MCP client. Best-effort; never blocks startup.
    try:
        register_mcp_servers(hub)
    except Exception:
        pass
    return hub
