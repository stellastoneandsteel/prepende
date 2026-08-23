"""tui — the goal-centric terminal app. The first surface, and the product.

What makes this unlike other TUIs (Claude Code, Codex, aider): they are
windows into a single conversation and forget you when they close. This is a
window into a BRAIN that persists — and the front door is GOAL-CENTRIC.

You state a goal; the TUI shows:
  - a living PLAN TREE (goal -> tasks -> sub-tasks, live status) shown SEPARATELY
    from the conversation (combining them fails as both),
  - the WORKSPACE filling up with real deliverables (artifacts), not just text,
  - the council/swarm deliberating in panes when a hard call convenes one,
  - what the brain just learned/remembered this session.

It calls the kernel IN-PROCESS (no HTTP needed locally) — the TUI is just
another consumer of the kernel, like the MCP server. Goals run durably and
resume after the terminal closes (DurableExecution + Workspace).

Stack: Python + Textual (MIT, by the Rich author) — rich widgets, async,
CSS-like styling for the dark aesthetic. Permissive, proprietary-safe.

SKELETON — minimal goal-centric chat + plan tree + workspace view in Phase 0.
The full surface (council view, artifact browser, connector palette) grows from there.
"""
