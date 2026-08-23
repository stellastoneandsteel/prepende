"""FilePromptRegistry — prompts as first-class, versioned, git-trackable artifacts.

Each prompt is a folder of versions (v1.txt, v2.txt, ...) under prompts/store/,
with an active-version pointer in active.json. Flipping the active version (or
rolling back) is a one-line pointer change, no redeploy. This is what the
self-improvement loop edits — never code, never guardrails.

Implements kernel.contracts.PromptRegistry (get / set_active / render) plus the
versioning helpers the improver needs.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from kernel.contracts import PromptRegistry
from prepende_brain.private_fs import (
    secure_directory,
    secure_file,
    secure_private_tree,
    write_private_text,
)


class FilePromptRegistry(PromptRegistry):
    def __init__(self, root: str = "./prompts/store") -> None:
        self.root = Path(root)
        if self.root.exists():
            secure_private_tree(self.root)
        else:
            secure_directory(self.root)
        self.active_file = self.root / "active.json"
        secure_file(self.active_file)

    # --- version storage ---
    def _dir(self, prompt_id: str) -> Path:
        if (
            not prompt_id
            or prompt_id in {".", ".."}
            or "/" in prompt_id
            or "\\" in prompt_id
            or "\x00" in prompt_id
        ):
            raise ValueError("invalid prompt identifier")
        d = self.root / prompt_id
        secure_directory(d, repair_existing=True)
        return d

    def versions(self, prompt_id: str) -> list[str]:
        return sorted((p.stem for p in self._dir(prompt_id).glob("v*.txt")), key=lambda s: int(s[1:]))

    def add_version(self, prompt_id: str, text: str) -> str:
        vs = self.versions(prompt_id)
        n = (int(vs[-1][1:]) + 1) if vs else 1
        v = f"v{n}"
        write_private_text(self._dir(prompt_id) / f"{v}.txt", text)
        return v

    def version_text(self, prompt_id: str, version: str) -> str:
        if not version.startswith("v") or not version[1:].isdigit():
            raise ValueError("invalid prompt version")
        p = self._dir(prompt_id) / f"{version}.txt"
        secure_file(p)
        return p.read_text() if p.exists() else ""

    def seed(self, prompt_id: str, text: str) -> str:
        if not self.versions(prompt_id):
            v = self.add_version(prompt_id, text)
            self._set_active(prompt_id, v)
        return self.active_version(prompt_id)

    # --- active pointer ---
    def _active_map(self) -> dict[str, str]:
        secure_file(self.active_file)
        return json.loads(self.active_file.read_text()) if self.active_file.exists() else {}

    def _set_active(self, prompt_id: str, version: str, scope: str | None = None) -> None:
        m = self._active_map()
        m[f"{prompt_id}@{scope}" if scope else prompt_id] = version
        write_private_text(self.active_file, json.dumps(m, indent=2))

    def active_version(self, prompt_id: str, scope: str | None = None) -> str | None:
        m = self._active_map()
        key = f"{prompt_id}@{scope}" if scope else prompt_id
        if key in m:
            return m[key]
        if scope and prompt_id in m:  # fall back to default-scope pointer
            return m[prompt_id]
        vs = self.versions(prompt_id)
        return vs[-1] if vs else None

    def list_prompts(self) -> list[str]:
        return sorted(p.name for p in self.root.iterdir() if p.is_dir())

    # --- contract (async) ---
    async def get(self, prompt_id: str, *, scope: str | None = None) -> str:
        v = self.active_version(prompt_id, scope)
        return self.version_text(prompt_id, v) if v else ""

    async def set_active(self, prompt_id: str, version: str, *, scope: str | None = None) -> None:
        self._set_active(prompt_id, version, scope)

    async def render(self, prompt_id: str, variables: dict[str, object], *, scope: str | None = None) -> str:
        text = await self.get(prompt_id, scope=scope)
        try:
            return text.format(**(variables or {}))
        except Exception:
            return text
