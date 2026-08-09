"""LocalWorkspace — the per-goal "space to work," on the local filesystem.

    <root>/<goal_id>/
        work/          sandbox working dir (shell/code later)
        artifacts/     deliverables (the output, not the chat)
        progress.md    durable run log so the goal can resume

Day-one implementation of the Workspace port. Container / remote sandbox is the
swap target behind this same interface. No per-task VM fleet (lean).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Sequence

from kernel.contracts import Workspace
from prepende_brain.private_fs import secure_directory, secure_file


class LocalWorkspace(Workspace):
    def __init__(self, root: str = "./.workspaces") -> None:
        self.root = Path(root)

    def _dir(self, goal_id: str) -> Path:
        return self.root / goal_id

    async def open(self, goal_id: str) -> Any:
        d = self._dir(goal_id)
        secure_directory(self.root)
        secure_directory(d)
        secure_directory(d / "work")
        secure_directory(d / "artifacts")
        pf = d / "progress.md"
        if not pf.exists():
            pf.write_text(f"# Goal {goal_id}\n\nStarted {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        secure_file(pf, required=True)
        return d

    async def run(self, command: str, *, timeout: int | None = None) -> Any:
        raise NotImplementedError("sandboxed shell/code execution lands in a later phase")

    async def write_artifact(self, goal_id: str, name: str, content: bytes | str) -> str:
        path = self._dir(goal_id) / "artifacts" / name
        secure_directory(path.parent)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content)
        secure_file(path, required=True)
        return str(path)

    async def list_artifacts(self, goal_id: str) -> Sequence[Any]:
        ad = self._dir(goal_id) / "artifacts"
        return [str(p) for p in sorted(ad.glob("*"))] if ad.exists() else []

    async def progress(self, goal_id: str, note: str) -> None:
        pf = self._dir(goal_id) / "progress.md"
        secure_directory(pf.parent)
        with pf.open("a") as f:
            f.write(f"- {time.strftime('%H:%M:%S')} {note}\n")
        secure_file(pf, required=True)
