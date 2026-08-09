"""chat_api — REMOTE human chat surface (later; not the first surface).

The first/local human surface is the in-process TUI (tui/). This is for when a
client connects to a SHARED brain over the network — a remote TUI, or a future
web/desktop client. A turn flows:

    POST /chat {message, conversation_id}  (or a WebSocket)
      -> kernel.core Goal Loop (Strategist picks a tactic; solo by default)
      -> ModelGateway.stream()  (whatever model is configured — any vendor/local)
      -> tokens streamed back over SSE/WebSocket
      -> turn written to MemoryStore so the brain remembers across sessions

Kept thin: a surface, not logic. It owns no reasoning — it calls the kernel
like any other consumer (mcp_server is its sibling). FastAPI (MIT) / Starlette
(BSD) / uvicorn (BSD) — permissive, fully proprietary-safe.

SKELETON — Phase 3+ (the local TUI ships first and needs none of this).
"""
