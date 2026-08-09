"""Workspace implementation — the per-goal "space to work."

Implements kernel.contracts.Workspace. Day-one: a local per-goal directory
  <root>/<goal_id>/
    work/          sandbox working dir (shell/code run here, bounded by policy)
    artifacts/     deliverables (docs, sheets, code, files) — the output
    progress.md    durable run log (+ git) so the goal resumes after a crash
plus tool/browser access borrowed through the Connectors port (MCP) — we do
NOT build our own browser/computer-use, and we do NOT run a per-task VM fleet.

THE MOAT: secrets never enter the sandbox; authenticated tool access comes
through scoped MCP connectors — so the brain can act in real logged-in tools
that isolated-sandbox agents (Genspark/Manus/ChatGPT Agent) cannot.

Swap target (same interface): a container / remote sandbox at scale.

SKELETON — Phase 0 (local dir + artifacts + progress); code-run hardening and
container option later.
"""
