"""Self-scraper — watch-listed sites stage brain-update drafts on change (W6).

Watched sites live in PREPENDE_WATCHED_SITES (JSON: {"tenant-scope": ["url", ...]}).
Each pass fetches every watched page, strips it to text, and compares against
the stored snapshot (.engram/watch/<scope>/<urlhash>.json):

  first scrape  -> the whole page text becomes a draft (everything is new to
                   the brain — the W6 diff still drops what memory already has)
  changed page  -> only the ADDED/CHANGED lines become a draft
  unchanged     -> nothing staged; freshness timestamp updates

Drafts ride knowledge/brain_update.py, so every staged item is an Assess
CANDIDATE with source "site_scrape:<url>" — the client approves in the same
review queue as everything else. Nothing here writes memory or sends anything.

The freshness ledger (lastScrapedAt / lastChangedAt per URL) is what the W4
brain-status panel surfaces as freshness flags.

Run: python3 scripts/brain_scraper.py   (one pass over every watched site)
"""

from __future__ import annotations

import difflib
import hashlib
import html as html_mod
import json
import os
import re
import time
import urllib.request
from pathlib import Path
from typing import Any

from knowledge.brain_update import draft_update
from prepende_brain.env import brand_env
from prepende_brain.private_fs import secure_directory, secure_file

_TAG_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.S | re.I)
_HTML_RE = re.compile(r"<[^>]+>")
_USER_AGENT = "PrependeKnowledgeWatcher/1.0"


def watched_sites() -> dict[str, list[str]]:
    raw = brand_env("WATCHED_SITES").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return {str(k): [str(u) for u in v] for k, v in data.items() if isinstance(v, list)}
    except Exception:
        return {}


def _watch_dir(scope: str) -> Path:
    base = Path(brand_env("WATCH_DIR", "./.engram/watch")) / scope
    return secure_directory(base)


def page_text(url: str, timeout: float = 15.0) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read(2_000_000).decode("utf-8", "replace")
    text = _TAG_RE.sub(" ", raw)
    text = _HTML_RE.sub(" ", text)
    text = html_mod.unescape(text)
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _state_path(scope: str, url: str) -> Path:
    return _watch_dir(scope) / (hashlib.sha256(url.encode()).hexdigest()[:16] + ".json")


def _load_state(scope: str, url: str) -> dict[str, Any]:
    p = _state_path(scope, url)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            pass
    return {}


def _save_state(scope: str, url: str, state: dict[str, Any]) -> None:
    path = _state_path(scope, url)
    path.write_text(json.dumps(state))
    secure_file(path, required=True)


def freshness(scope: str) -> list[dict[str, Any]]:
    """The ledger the brain-status panel reads: per watched URL, when it was
    last checked and when its content last actually changed."""
    out = []
    for url in watched_sites().get(scope, []):
        state = _load_state(scope, url)
        out.append({
            "url": url,
            "lastScrapedAt": state.get("lastScrapedAt"),
            "lastChangedAt": state.get("lastChangedAt"),
            "everScraped": bool(state),
        })
    return out


async def scrape_once(brain: Any, scope: str, url: str) -> dict[str, Any]:
    """One page, one pass: fetch, diff vs snapshot, stage changed lines."""
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    try:
        text = page_text(url)
    except Exception as exc:
        state = _load_state(scope, url)
        state["lastScrapedAt"] = now
        state["lastError"] = f"{type(exc).__name__}: {exc}"
        _save_state(scope, url, state)
        return {"url": url, "ok": False, "error": state["lastError"], "staged": 0}

    digest = hashlib.sha256(text.encode()).hexdigest()
    state = _load_state(scope, url)
    previous = state.get("snapshot", "")
    changed = digest != state.get("hash")

    staged = 0
    receipt: dict[str, Any] | None = None
    if changed:
        if previous:
            added = [
                line[2:] for line in difflib.ndiff(previous.splitlines(), text.splitlines())
                if line.startswith("+ ") and len(line[2:].strip()) >= 8
            ]
            delta = "\n".join(added)
        else:
            delta = text  # first scrape: the brain-update diff filters known facts
        if delta.strip():
            receipt = await draft_update(brain, scope, delta, source=f"site_scrape:{url}")
            staged = receipt["counts"]["new"] + receipt["counts"]["update"]
        state["lastChangedAt"] = now
    state.update({"hash": digest, "snapshot": text[:200_000], "lastScrapedAt": now})
    state.pop("lastError", None)
    _save_state(scope, url, state)
    return {"url": url, "ok": True, "changed": changed, "staged": staged,
            "draft": receipt and {"counts": receipt["counts"]}}


async def scrape_all(brain: Any) -> list[dict[str, Any]]:
    results = []
    for scope, urls in watched_sites().items():
        for url in urls:
            out = await scrape_once(brain, scope, url)
            out["scope"] = scope
            results.append(out)
    return results
