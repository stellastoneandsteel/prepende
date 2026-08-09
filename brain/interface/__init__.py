"""Inbound surfaces — how callers reach the brain.

The FIRST/local human surface is NOT here — it is the goal-centric TUI (tui/),
which calls the kernel IN-PROCESS (no HTTP needed locally). This package holds
the REMOTE inbound surfaces:

1. http_api    — LIVE. A stdlib (zero-dep) HTTP remote API: POST /goal, GET
   /memory, GET /health, optional bearer-token auth. Real "remote control" of
   the brain from another machine. Verified by tests/smoke_http.py.

2. mcp_server  — LIVE. An MCP server so any compliant agent (OpenClaw included)
   connects with zero brain-side custom code: memory -> resources, reason/write
   -> tools. Needs the `mcp` SDK (pip install mcp); same brain behind it.

3. chat_api    — for HUMANS, REMOTELY, with streaming (later; superseded for now
   by the stdlib http_api).

All are just consumers of the kernel; scoping enforced per handler. Proprietary
reasoning and model-swap machinery stay entirely behind them.
"""
