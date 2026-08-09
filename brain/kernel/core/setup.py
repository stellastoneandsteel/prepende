"""Model Concierge setup helpers (Phase 2). Takes a user from echo/broken to a
verified working model. See docs/MODEL-CONCIERGE-DESIGN.md.

SECURITY: the mutating flows (apply/secret) write .env and are unauthenticated by
necessity (no token exists before setup), so the HTTP layer hard-gates them to a
loopback client on a LOCAL runtime. This module's job is to be key-safe: the
`secret` path writes a key to .env and updates the in-process value, and returns
ONLY presence booleans — never the value, never logged. `setup_state` is fully
key-free (presence booleans only) and safe to serialize anywhere.

Stdlib only.
"""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from typing import Any

from kernel.core.config import Config
from kernel.core.context import classify_provider, detect_context, detect_runtime
from prepende_brain.private_fs import secure_file

# provider -> the .env var that holds its key (None = no key needed).
_KEY_VAR: dict[str, str | None] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "google": "GOOGLE_API_KEY",
    "openai-compatible": "OPENAI_COMPATIBLE_API_KEY",
    "local": None,
    "echo": None,
    "cli-claude": None,
    "cli-codex": None,
}

# provider -> the Config attribute holding its key, so a live swap sees the new key
# without a restart.
_KEY_ATTR: dict[str, str] = {
    "anthropic": "anthropic_key",
    "openai": "openai_key",
    "google": "google_key",
    "openai-compatible": "openai_compat_key",
}

_SETTABLE = ("echo", "anthropic", "openai", "google", "openai-compatible", "local", "cli-claude", "cli-codex")


def key_var_for(provider: str) -> str | None:
    return _KEY_VAR.get((provider or "").strip().lower())


def _clean(v: str) -> str:
    """No newlines/control chars — they would corrupt .env. Trim surrounding quotes."""
    return (v or "").replace("\r", "").replace("\n", "").strip().strip('"').strip("'")


def write_env(updates: dict[str, str], path: str = ".env") -> None:
    """Idempotently set KEY=VALUE in .env: replace existing keys in place, append
    new ones, preserve everything else (comments, other vars, order)."""
    p = Path(path)
    if p.exists() or p.is_symlink():
        # Refuse before reading or writing: checking only after write would let
        # a symlink redirect credentials into an unrelated operator file.
        secure_file(p, required=True)
    lines = p.read_text().splitlines() if p.exists() else []
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            k = s.split("=", 1)[0].strip()
            if k in updates:
                out.append(f"{k}={_clean(updates[k])}")
                seen.add(k)
                continue
        out.append(line)
    for k, v in updates.items():
        if k not in seen:
            out.append(f"{k}={_clean(v)}")
    p.write_text("\n".join(out) + "\n")
    secure_file(p, required=True)


def provider_ready(cfg: Config, provider: str) -> bool:
    """Configured ENOUGH to switch to — by presence only (key/base/binary). Key-safe."""
    p = (provider or "").strip().lower()
    if p in ("echo", "local"):
        return True
    if p in ("cli-claude", "cli-codex"):
        return bool(shutil.which("claude" if "claude" in p else "codex"))
    if p == "anthropic":
        return bool(cfg.anthropic_key)
    if p == "openai":
        return bool(cfg.openai_key)
    if p == "google":
        return bool(cfg.google_key)
    if p == "openai-compatible":
        return bool(cfg.openai_compat_base)
    return False


def setup_state(cfg: Config, gateway: Any, *, loopback: bool) -> dict[str, Any]:
    """Key-free readiness snapshot for the setup UI. canSelfConfigure is true only
    when the request is loopback AND the runtime is local — i.e. a self-hoster who
    can safely write .env. Hosted tenants get the env-var instructions instead."""
    ctx = detect_context(cfg, gateway)
    is_local = ctx["runtime"]["location"] == "local"
    providers = []
    for p in _SETTABLE:
        cls = classify_provider(p)
        providers.append({
            "provider": p,
            "locality": cls["locality"],
            "authMode": cls["authMode"],
            "configured": provider_ready(cfg, p),
            "needsKey": key_var_for(p) is not None,
            "keyVar": key_var_for(p),
        })
    return {
        "context": ctx,
        "active": getattr(gateway, "name", cfg.provider),
        "deployment": ctx["runtime"]["location"],
        "canSelfConfigure": bool(loopback and is_local),
        "providers": providers,
    }


def verify_gateway(gateway: Any) -> dict[str, Any]:
    """One tiny live call to confirm the model actually responds. Surfaces the error
    CLASS, never the key. Synchronous wrapper around the async gateway."""
    import asyncio
    t0 = time.monotonic()
    try:
        asyncio.run(gateway.complete([{"role": "user", "content": "Reply with the single word READY."}], max_tokens=5))
        return {"ok": True, "latencyMs": int((time.monotonic() - t0) * 1000),
                "model": getattr(gateway, "name", "?"), "detail": "Live call succeeded."}
    except Exception as e:  # noqa: BLE001 — class only, never the key
        return {"ok": False, "errorClass": type(e).__name__, "model": getattr(gateway, "name", "?"),
                "detail": "Live call failed — check the key / model name / that the model server is running."}


def validate_provider(provider: str) -> bool:
    return (provider or "").strip().lower() in _SETTABLE
