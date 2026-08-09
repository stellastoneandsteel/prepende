"""Context detection — where is Engram running, and is its model reachable?

The spine of the Model Concierge (see docs/MODEL-CONCIERGE-DESIGN.md). Pure
functions over (Config, ModelGateway) with at most ONE bounded network call.

Two invariants this module exists to make structural:
  1. KEY-SAFE: it reads keys ONLY via bool(presence) — never a value. The output
     is safe to serialize to any surface (a smoke test asserts no key-shaped
     string ever appears in it).
  2. HONEST ABOUT PHYSICS: a hosted brain cannot reach a model on a user's
     laptop. When the runtime is hosted but the configured model is local/CLI,
     the probe reports status="unreachable_by_design" with the plain reason,
     so the setup + advisor surfaces key off it instead of papering over it.

Stdlib only. No new dependencies.
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any

from kernel.core.config import Config
from models._http import get_json

# provider -> (model locality, auth mode). Mirrors the EXACT strings
# models/factory.build_gateway switches on, so the detector and the factory
# stay in lockstep. auth modes use the contracts/model.py taxonomy.
_PROVIDER_CLASS: dict[str, tuple[str, str]] = {
    "echo": ("stub", "none"),
    "auto": ("hosted", "multi_provider"),
    "best-available": ("hosted", "multi_provider"),
    "anthropic": ("hosted", "api_key"),
    "openai": ("hosted", "api_key"),
    "google": ("hosted", "api_key"),
    "grok": ("hosted", "api_key"),
    "xai": ("hosted", "api_key"),
    "openai-compatible": ("hosted", "api_key"),
    "local": ("local", "local"),
    # Subscription-via-CLI runs the vendor CLI on THIS machine (local) billing a
    # flat sub; never offerable to a hosted tenant (vendor ToS — see models/cli.py).
    "cli-claude": ("local", "subscription"),
    "claude-cli": ("local", "subscription"),
    "cli-codex": ("local", "subscription"),
    "codex-cli": ("local", "subscription"),
}

# Hosting-platform env vars: presence is a strong "this is a server" signal.
_PAAS_VARS = (
    "RAILWAY_ENVIRONMENT", "RAILWAY_PROJECT_ID", "RAILWAY_SERVICE_ID",
    "RENDER", "FLY_APP_NAME", "DYNO", "K_SERVICE", "VERCEL", "HEROKU_APP_NAME",
)

_PROBE_TTL_S = 30.0
_probe_cache: dict[str, Any] = {"at": 0.0, "key": None, "val": None}


def classify_provider(provider: str | None) -> dict[str, str]:
    """Map a provider string to {provider, locality, authMode}. Never networks."""
    p = (provider or "").strip().lower()
    locality, auth = _PROVIDER_CLASS.get(p, ("unknown", "unknown"))
    return {"provider": p, "locality": locality, "authMode": auth}


def _provider_configured(cfg: Config, provider: str) -> bool:
    """Is this provider configured ENOUGH to attempt — by key/base PRESENCE only.
    Reads bool() of secrets, never a value."""
    p = (provider or "").strip().lower()
    if p == "anthropic":
        return bool(cfg.anthropic_key)
    if p == "openai":
        return bool(cfg.openai_key)
    if p == "google":
        return bool(cfg.google_key)
    if p in {"grok", "xai"}:
        return bool(cfg.xai_key)
    if p == "openai-compatible":
        return bool(cfg.openai_compat_base)  # base required; key optional
    if p in {"auto", "best-available"}:
        return any((cfg.anthropic_key, cfg.openai_key, cfg.xai_key, cfg.google_key))
    # echo / local / cli-* need no API key
    return True


def detect_runtime(cfg: Config) -> dict[str, Any]:
    """Best-honest verdict (with evidence) on local-vs-hosted. Never a silent
    single-bit guess: it weighs independent signals and returns 'unknown' on
    conflict, exposing the raw signals so a human (or the setup UI) can decide."""
    signals: list[str] = []
    hosted = 0.0
    local = 0.0

    paas = [v for v in _PAAS_VARS if os.environ.get(v)]
    if paas:
        hosted += 2.0
        signals.append("paas_env(" + ",".join(paas) + ")")
    if os.path.exists("/.dockerenv"):
        hosted += 1.0
        signals.append("dockerenv")

    try:
        tty = sys.stdin.isatty()
    except Exception:
        tty = False
    if tty:
        local += 1.0
        signals.append("interactive_tty")
    else:
        hosted += 0.5
        signals.append("no_tty")

    base = cfg.local_base or ""
    loopback = ("localhost" in base) or ("127.0.0.1" in base)
    if loopback:
        local += 1.0
        signals.append("loopback_local_base")
    if sys.platform == "darwin":
        local += 0.5
        signals.append("darwin")

    if hosted >= 2.0 and hosted > local:
        location, confidence = "hosted", ("high" if paas else "medium")
    elif local >= 1.5 and local > hosted:
        location, confidence = "local", ("high" if (tty and loopback) else "medium")
    else:
        location, confidence = "unknown", "low"

    return {"location": location, "confidence": confidence, "signals": signals}


def _model_base(cfg: Config, gateway: Any) -> str:
    return (getattr(gateway, "base", "") or cfg.local_base or "").rstrip("/")


def probe_model(cfg: Config, gateway: Any, runtime: dict[str, Any] | None = None, verify: bool = False) -> dict[str, Any]:
    """Classify the configured model's status. At most ONE 2s network call (only
    for a local server, or for ?verify=1). Cached ~30s unless verify=True."""
    runtime = runtime or detect_runtime(cfg)
    cls = classify_provider(cfg.provider)
    locality, auth = cls["locality"], cls["authMode"]
    name = getattr(gateway, "name", cfg.provider)
    model = getattr(gateway, "model", "") or cfg.model or ""

    def out(status: str, detail: str, latency_ms: int | None = None) -> dict[str, Any]:
        return {
            "provider": name, "model": model, "locality": locality, "authMode": auth,
            "status": status, "latencyMs": latency_ms, "detail": detail,
        }

    # Physics guard FIRST: a hosted brain can't reach a laptop-local model/CLI.
    if runtime.get("location") == "hosted" and locality == "local":
        return out("unreachable_by_design",
                   "A hosted brain cannot reach a model running on your machine. Run Engram locally to use a local or CLI model.")

    if locality == "stub":
        return out("stub", "Echo stub — runs anywhere at $0, but it is not a real model. Set MODEL_PROVIDER + a model to go live.")

    if auth in {"api_key", "multi_provider"} and not _provider_configured(cfg, cfg.provider):
        return out("unconfigured", f"No key configured for '{cfg.provider}'. Add it (never through the assistant) to go live.")

    # cache key (verify is always fresh)
    if not verify:
        ckey = f"{cfg.provider}|{_model_base(cfg, gateway)}|{model}"
        now = time.monotonic()
        if _probe_cache["key"] == ckey and (now - _probe_cache["at"]) < _PROBE_TTL_S:
            return dict(_probe_cache["val"])

    result: dict[str, Any]
    if locality == "local":
        base = _model_base(cfg, gateway)
        url = base + "/models"
        t0 = time.monotonic()
        code, reachable = get_json(url, timeout=2)
        ms = int((time.monotonic() - t0) * 1000)
        if reachable and (code is None or 200 <= code < 300):
            result = out("healthy", f"Local model server responding at {base}.", ms)
        elif reachable:
            result = out("reachable_no_model", f"A server is up at {base} but returned {code}. Is a model pulled (e.g. `ollama pull llama3.1:8b`)?", ms)
        else:
            result = out("unreachable", f"No local model server at {base}. Is Ollama / LM Studio running?", ms)
    elif verify:
        # One tiny live call (opt-in). Confirms a hosted key/CLI actually works.
        import asyncio
        t0 = time.monotonic()
        try:
            asyncio.run(gateway.complete([{"role": "user", "content": "Reply with the single word READY."}], max_tokens=5))
            ms = int((time.monotonic() - t0) * 1000)
            result = out("healthy", "Verified with a live call.", ms)
        except Exception as e:  # noqa: BLE001 — surface the class, never the key
            result = out("error", f"Live call failed ({type(e).__name__}). Check the key / model name / connection.")
    else:
        result = out("configured", f"'{cfg.provider}' is configured (key present); not verified, no token spent. Add ?verify=1 to confirm with one live call.")

    if not verify:
        _probe_cache.update({"at": time.monotonic(), "key": f"{cfg.provider}|{_model_base(cfg, gateway)}|{model}", "val": dict(result)})
    return result


def detect_context(cfg: Config, gateway: Any, verify: bool = False) -> dict[str, Any]:
    """The public entry: runtime location + model status, key-safe + serializable."""
    runtime = detect_runtime(cfg)
    return {"runtime": runtime, "model": probe_model(cfg, gateway, runtime=runtime, verify=verify)}
