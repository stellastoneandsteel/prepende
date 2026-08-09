"""SemanticConsolidator — topic-level memory consolidation (model-driven).

The store's `consolidate()` dedups *near-duplicates* deterministically. This is
the next rung the longitudinal study pointed at: merging same-topic *distinct*
facts (e.g. four separate notes about one project's backup setup) into one denser
memory. That needs semantic understanding, so the grouping is done by the model.

Separation: the store keeps owning the safe supersede mechanics; this component
only injects intelligence. It is given a ModelGateway INSTANCE (no model SDK is
imported here — tiering rule intact) and exposes a `grouper` + `summarizer` that
the store calls. Conservative by construction, non-destructive (supersede, not
delete), reversible, and opt-in — never run automatically on the live brain.
"""

from __future__ import annotations

import json
from typing import Any

_MAX_NOTE_CHARS = 400  # keep the grouping prompt bounded


class SemanticConsolidator:
    def __init__(self, gateway: Any) -> None:
        self.gateway = gateway

    async def group(self, contents: list[str]) -> list[list[int]]:
        """Conservatively cluster note indices by shared narrow topic."""
        if len(contents) < 2:
            return [[i] for i in range(len(contents))]
        listing = "\n".join(f"{i}: {c[:_MAX_NOTE_CHARS]}" for i, c in enumerate(contents))
        prompt = (
            "Below are short memory notes, numbered. Group notes that are about the "
            "SAME specific topic and could be merged into one denser note without "
            "losing anything. Be CONSERVATIVE: notes about different topics, "
            "entities, numbers, or facts MUST stay in separate groups. When unsure, "
            "keep a note in its own group.\n\n"
            "Return STRICT JSON only: a list of groups, each a list of note numbers, "
            "e.g. [[0,3],[1],[2,4]]. Every number from 0 to "
            f"{len(contents) - 1} appears exactly once.\n\nNotes:\n{listing}\n"
        )
        raw = await self.gateway.complete([{"role": "user", "content": prompt}], max_tokens=400)
        return _parse_groups(raw)

    async def summarize(self, contents: list[str]) -> str:
        """Merge same-topic notes into one faithful, denser note."""
        bullets = "\n".join(f"- {c}" for c in contents)
        prompt = (
            "Merge these notes, which are about one topic, into a SINGLE concise "
            "note. Include every distinct fact, name, and number from the "
            "originals. Invent nothing; drop nothing important. Return only the "
            f"merged note text, no preamble.\n\nNotes:\n{bullets}\n"
        )
        raw = await self.gateway.complete([{"role": "user", "content": prompt}], max_tokens=400)
        return str(raw).strip()

    async def consolidate(self, store: Any, *, scope: str) -> dict[str, Any]:
        """Run topic consolidation on `scope` of `store`. Returns the store report."""
        return await store.consolidate(scope=scope, grouper=self.group, summarizer=self.summarize)


def _parse_groups(raw: Any) -> list[list[int]]:
    s = str(raw)
    i, j = s.find("["), s.rfind("]")
    if i < 0 or j <= i:
        return []
    try:
        data = json.loads(s[i:j + 1])
    except Exception:
        return []
    out: list[list[int]] = []
    if isinstance(data, list):
        for g in data:
            if isinstance(g, (list, tuple)):
                members = []
                for x in g:
                    try:
                        members.append(int(x))
                    except Exception:
                        continue
                if members:
                    out.append(members)
    return out
