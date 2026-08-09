"""Intake gate — one acceptance policy for untrusted content entering the brain.

Every cockpit that stages externally-sourced content (the MCP `memory_propose`
connector path, the operator `ingest_knowledge` lane, the file bridges) runs the
same scan, so policy can't drift between surfaces.

Two product-agnostic checks (the engram core names NO product):
  - injection: directive-looking payloads embedded in staged content. Staged
    content is DATA, never instructions; this flags attempts to make it read as a
    command (so an inattentive reviewer / a downstream template can't be steered).
  - blocked: deploy-configured forbidden terms. A deployment that must keep
    specific third-party IP out of its brain sets ENGRAM_INTAKE_BLOCKLIST
    (comma-separated) or passes extra_blocklist; the core ships with NONE.

Stdlib only. Pure functions — testable without infra.
"""

from __future__ import annotations

import os
import re
from typing import Any, Iterable

# Specific injection idioms — kept tight to avoid flagging ordinary research prose.
_INJECTION = re.compile(
    r"(ignore\s+(all\s+|the\s+)?(previous|prior|above)\s+(instructions|prompts?)"
    r"|disregard\s+(the\s+)?(system|previous|above)"
    r"|\[\s*(system|assistant|admin)[\s\-]*(override|prompt|message)"
    r"|```\s*(workflow|tool|system)\b"
    r"|<\|im_(start|end)\|>"
    r"|new\s+instructions\s*:"
    r"|system\s+override)",
    re.I,
)


def _blocklist(extra: Iterable[str] = ()) -> list[str]:
    env = (os.environ.get("ENGRAM_INTAKE_BLOCKLIST") or "").strip()
    terms = [t.strip() for t in env.split(",") if t.strip()]
    terms += [str(t).strip() for t in extra if str(t).strip()]
    return terms


def scan_intake(text: str, *, extra_blocklist: Iterable[str] = ()) -> dict[str, list[str]]:
    """Scan untrusted content. Returns {'injection': [...snippets], 'blocked': [...terms]}.
    Empty lists mean clean."""
    t = text or ""
    injection = sorted({m.group(0).strip()[:60] for m in _INJECTION.finditer(t)})
    low = t.lower()
    blocked = sorted({term for term in _blocklist(extra_blocklist) if term.lower() in low})
    return {"injection": injection, "blocked": blocked}


def is_clean(scan: dict[str, Any]) -> bool:
    return not scan.get("injection") and not scan.get("blocked")
