"""Intake gate — one acceptance policy for untrusted content entering the brain.

Every cockpit that stages externally-sourced content (the MCP `memory_propose`
connector path, the operator `ingest_knowledge` lane, the file bridges) runs the
same scan, so policy can't drift between surfaces.

Two product-agnostic checks (the engram core names NO product):
  - injection: directive-looking payloads embedded in staged content. Staged
    content is DATA, never instructions; this flags attempts to make it read as a
    command (so an inattentive reviewer / a downstream template can't be steered).
  - blocked: deploy-configured forbidden terms. A deployment that must keep
    specific third-party IP out of its brain sets PREPENDE_INTAKE_BLOCKLIST
    (comma-separated) or passes extra_blocklist; the core ships with NONE.

Before either check, bounded canonical views close common representation
bypasses: nested HTML entities, tags inserted within or between tokens,
Unicode compatibility forms/case variants, and invisible format controls.

Stdlib only. Pure functions — testable without infra.
"""

from __future__ import annotations

import html
import re
import unicodedata
from typing import Any, Iterable

from prepende_brain.env import brand_env

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

_HTML_TAG = re.compile(r"<[^>]*>", re.S)
_WHITESPACE = re.compile(r"\s+")
_MAX_ENTITY_DECODE_PASSES = 8
_ENTITY_DECODE_LIMIT_MARKER = "entity_decode_limit_exceeded"


def _decode_entities(value: str) -> tuple[str, bool]:
    """Decode to a bounded fixed point and report unresolved nesting."""

    decoded = value
    for _ in range(_MAX_ENTITY_DECODE_PASSES):
        next_value = html.unescape(decoded)
        if next_value == decoded:
            return decoded, False
        decoded = next_value
    return decoded, html.unescape(decoded) != decoded


def _replace_format_controls(value: str, replacement: str) -> str:
    """Remove or separate invisible Unicode format controls (category Cf)."""

    return "".join(
        replacement if unicodedata.category(character) == "Cf" else character
        for character in value
    )


def _canonical_views(text: str) -> tuple[tuple[str, ...], bool]:
    """Return bounded canonical views that close markup/Unicode token splits.

    Both joined and separated forms matter: ``ign<span>ore`` becomes ``ignore``
    while ``ignore<br>all`` becomes ``ignore all``. The raw decoded view also
    retains tag attributes so directive text hidden there is still scanned.
    """

    decoded_entities, decode_limit_exceeded = _decode_entities(text or "")
    decoded = unicodedata.normalize("NFKC", decoded_entities).casefold()
    markup_views = (
        decoded,
        _HTML_TAG.sub("", decoded),
        _HTML_TAG.sub(" ", decoded),
    )
    views: set[str] = set()
    for value in markup_views:
        for replacement in ("", " "):
            normalized = _replace_format_controls(value, replacement)
            normalized = _WHITESPACE.sub(" ", normalized).strip()
            if normalized:
                views.add(normalized)
    return tuple(sorted(views)), decode_limit_exceeded


def _blocklist(extra: Iterable[str] = ()) -> list[str]:
    env = brand_env("INTAKE_BLOCKLIST")
    terms = [t.strip() for t in env.split(",") if t.strip()]
    terms += [str(t).strip() for t in extra if str(t).strip()]
    return terms


def scan_intake(text: str, *, extra_blocklist: Iterable[str] = ()) -> dict[str, list[str]]:
    """Scan untrusted content. Returns {'injection': [...snippets], 'blocked': [...terms]}.
    Empty lists mean clean."""
    views, decode_limit_exceeded = _canonical_views(text or "")
    injection_matches = {
        match.group(0).strip()[:60]
        for view in views
        for match in _INJECTION.finditer(view)
    }
    if decode_limit_exceeded:
        injection_matches.add(_ENTITY_DECODE_LIMIT_MARKER)
    injection = sorted(injection_matches)
    blocked: list[str] = []
    for term in _blocklist(extra_blocklist):
        term_views, term_decode_limit_exceeded = _canonical_views(term)
        if term_decode_limit_exceeded:
            blocked.append(term)
            continue
        if any(term_view in view for term_view in term_views for view in views):
            blocked.append(term)
    blocked = sorted(set(blocked))
    return {"injection": injection, "blocked": blocked}


def is_clean(scan: dict[str, Any]) -> bool:
    return not scan.get("injection") and not scan.get("blocked")
