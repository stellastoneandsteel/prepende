"""Workspace — the brain's "space to work," scoped per goal.

The lesson from every goal-centric agent (Genspark, Manus, ChatGPT Agent,
Devin, Cursor): an autonomous agent needs a place to actually DO work and
leave real deliverables — not just chat. A Workspace is allocated per Goal Loop
run and contains:

  - a sandbox working directory   (isolated per goal, persisted, resumable)
  - artifacts/                    (the deliverables: docs, sheets, code, files
                                   — our equivalent of Genspark's "AI Drive")
  - shell / code execution        (with the hard ceilings from policy)
  - tools + browser via MCP        (borrowed through the Connectors port — we
                                   do NOT build our own browser/computer-use)
  - progress.md + git history     (so a goal survives crashes and RESUMES;
                                   this is the compounding run-memory made
                                   concrete, and pairs with DurableExecution)

THE MOAT: credentials stay OUTSIDE the sandbox. Other agents' isolated
sandboxes can't touch your logged-in tools; ours reaches real, authenticated
tools through scoped MCP connectors without secrets ever entering the
workspace. That sidesteps the exact weakness limiting Genspark/Manus/ChatGPT
Agent.

Day-one impl: a local per-goal directory + git. Swap target: a container /
remote sandbox later, behind this same interface. We do NOT build a per-task
VM fleet — lean.

Impl: workspace/   Used by: kernel/core/ (the Goal Loop) and tactics/
SKELETON — signatures only, no implementation yet.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Sequence


class Workspace(ABC):
    """A per-goal place to work. Isolated, persisted, resumable."""

    @abstractmethod
    async def open(self, goal_id: str) -> Any:
        """Allocate (or resume) the workspace for a goal. Returns a handle."""

    @abstractmethod
    async def run(self, command: str, *, timeout: int | None = None) -> Any:
        """Execute shell/code in the sandbox, bounded by policy ceilings."""

    @abstractmethod
    async def write_artifact(self, path: str, content: bytes | str) -> str:
        """Store a deliverable (doc, sheet, code, file). Returns its artifact id."""

    @abstractmethod
    async def list_artifacts(self, goal_id: str) -> Sequence[Any]:
        """The deliverables produced for a goal — the output, not the chat."""

    @abstractmethod
    async def progress(self, goal_id: str, note: str) -> None:
        """Append to the durable progress log (progress.md + git) so the run resumes."""
