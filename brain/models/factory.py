"""build_gateway — select the model adapter from config. The swap point.

Changing MODEL_PROVIDER in .env changes the model with no code edit. This is
where "use whatever AI you want" becomes literally true.
"""

from __future__ import annotations

import os

from kernel.contracts import ModelGateway
from kernel.core.config import Config
from models.echo import EchoGateway
from models.anthropic import AnthropicGateway
from models.openai import OpenAIGateway
from prepende_brain.env import brand_env
from models.google import GoogleGateway
from models.catalog import model_fallbacks, resolve_model_id


def _build_auto_gateway(cfg: Config) -> ModelGateway:
    """Build only the provider lanes for which this host has credentials."""

    from models.auto import AutoGateway

    route = []
    seen: set[str] = set()
    for raw_provider in cfg.model_route.split(","):
        provider = raw_provider.strip().lower()
        if provider == "xai":
            provider = "grok"
        if not provider or provider in seen:
            continue
        if provider not in {"anthropic", "openai", "grok", "google"}:
            raise ValueError(
                f"unsupported provider '{provider}' in PREPENDE_MODEL_ROUTE"
            )
        seen.add(provider)
        if provider == "anthropic" and cfg.anthropic_key:
            model = resolve_model_id("anthropic", cfg.anthropic_model)
            route.append(
                AnthropicGateway(
                    cfg.anthropic_key,
                    model,
                    model_fallbacks("anthropic", model),
                )
            )
        elif provider == "openai" and cfg.openai_key:
            model = resolve_model_id("openai", cfg.openai_model)
            route.append(
                OpenAIGateway(
                    cfg.openai_key,
                    model,
                    "https://api.openai.com/v1",
                    "openai",
                    model_fallbacks("openai", model),
                )
            )
        elif provider == "grok" and cfg.xai_key:
            route.append(
                OpenAIGateway(
                    cfg.xai_key,
                    cfg.grok_model,
                    "https://api.x.ai/v1",
                    "grok",
                )
            )
        elif provider == "google" and cfg.google_key:
            route.append(GoogleGateway(cfg.google_key, cfg.google_model))

    if not route:
        raise RuntimeError(
            "MODEL_PROVIDER=auto found no configured providers. Set at least one "
            "of ANTHROPIC_API_KEY, OPENAI_API_KEY, XAI_API_KEY, or GOOGLE_API_KEY."
        )
    return AutoGateway(route)


def build_gateway(cfg: Config, provider: str | None = None, model: str | None = None) -> ModelGateway:
    """Select a model adapter. `provider`/`model` override cfg for a SECONDARY
    gateway (e.g. an independent embedding provider, per kernel/contracts/model.py);
    default to cfg's generation provider/model so existing callers are unchanged.
    Generation and embeddings are interchangeable across claude/openai/local here."""
    p = (provider or cfg.provider).lower()
    if p in {"auto", "best-available"}:
        return _build_auto_gateway(cfg)
    requested_model = cfg.model if model is None else model
    m = resolve_model_id(p, requested_model)

    if p == "echo":
        return EchoGateway()

    if p == "anthropic":
        if not cfg.anthropic_key:
            raise RuntimeError("ANTHROPIC_API_KEY missing. Set it in .env, or use MODEL_PROVIDER=echo.")
        return AnthropicGateway(cfg.anthropic_key, m, model_fallbacks(p, m))

    if p == "openai":
        if not cfg.openai_key:
            raise RuntimeError("OPENAI_API_KEY missing. Set it in .env, or use MODEL_PROVIDER=echo.")
        return OpenAIGateway(cfg.openai_key, m, "https://api.openai.com/v1", "openai", model_fallbacks(p, m))

    if p == "google":
        if not cfg.google_key:
            raise RuntimeError("GOOGLE_API_KEY missing. Set it in .env, or use MODEL_PROVIDER=echo.")
        return GoogleGateway(cfg.google_key, m)

    # Grok (xAI). OpenAI-compatible API, so it reuses the OpenAI gateway with the
    # xAI base_url. Set MODEL_PROVIDER=grok and XAI_API_KEY in .env (key: console.x.ai).
    if p in ("grok", "xai"):
        if not cfg.xai_key:
            raise RuntimeError("XAI_API_KEY missing. Set it in .env (get a key at console.x.ai), or use MODEL_PROVIDER=echo.")
        return OpenAIGateway(cfg.xai_key, cfg.model or "grok-2-latest", "https://api.x.ai/v1", "grok")

    if p == "openai-compatible":
        if not cfg.openai_compat_base:
            raise RuntimeError("OPENAI_COMPATIBLE_BASE_URL missing (e.g. https://openrouter.ai/api/v1).")
        return OpenAIGateway(cfg.openai_compat_key, m, cfg.openai_compat_base, "openai-compatible")

    if p == "local":
        return OpenAIGateway("", m or "llama3", cfg.local_base, "local")

    # Subscription-via-CLI (personal use): bill your flat sub, not API tokens.
    if p in ("cli-claude", "claude-cli"):
        from models.cli import CliGateway
        cmd = ["claude", "-p"]
        # Opt-in per session: PREPENDE_CLI_ALLOWED_TOOLS="WebSearch" grants the
        # nested CLI specific tools so brain agents can actually source claims
        # (research needs live search). Equals-form so the trailing prompt
        # argument is never swallowed by the variadic flag. Unset -> unchanged.
        tools = brand_env("CLI_ALLOWED_TOOLS").strip()
        if tools:
            cmd.append(f"--allowedTools={tools}")
        if m:
            cmd.extend(["--model", m])
        return CliGateway(cmd, "claude-sub", m, model_fallbacks(p, m))
    if p in ("cli-codex", "codex-cli"):
        from models.cli import CliGateway
        cmd = ["codex", "exec"]
        if m:
            cmd.extend(["-m", m])
        return CliGateway(cmd, "codex-sub", m, model_fallbacks(p, m))

    raise RuntimeError(
        f"Unknown provider '{p}'. Use one of: "
        "auto | echo | anthropic | openai | google | grok | openai-compatible | "
        "local | cli-claude | cli-codex."
    )


def build_gateway_from(provider: str, model: str = "", key: str = "", base: str = "",
                       local_base: str = "http://localhost:11434/v1") -> ModelGateway:
    """Build a gateway from EXPLICIT values (not a Config) — the BYO-brain entry.

    Used by kernel/core/tenant_runtime.py to build a per-tenant GENERATION gateway
    from a tenant's chosen provider/model and their decrypted key. Deliberately
    EXCLUDES cli-* (subscription-via-CLI is local+personal, never offerable to a
    hosted tenant — vendor ToS). `local` is supported for self-host but a hosted
    tenant is refused upstream (a hosted brain can't reach a laptop model)."""
    p = (provider or "").lower()
    m = resolve_model_id(p, model)
    if p == "echo":
        return EchoGateway()
    if p == "anthropic":
        if not key:
            raise RuntimeError("anthropic requires an API key")
        return AnthropicGateway(key, m, model_fallbacks(p, m))
    if p == "openai":
        if not key:
            raise RuntimeError("openai requires an API key")
        return OpenAIGateway(key, m, "https://api.openai.com/v1", "openai", model_fallbacks(p, m))
    if p == "google":
        if not key:
            raise RuntimeError("google requires an API key")
        return GoogleGateway(key, m)
    if p in ("grok", "xai"):
        if not key:
            raise RuntimeError("grok requires an API key")
        return OpenAIGateway(key, m or "grok-2-latest", "https://api.x.ai/v1", "grok")
    if p == "openai-compatible":
        if not base:
            raise RuntimeError("openai-compatible requires a base URL")
        return OpenAIGateway(key, m, base, "openai-compatible")
    if p == "local":
        return OpenAIGateway("", m or "llama3", base or local_base, "local")
    raise RuntimeError(
        f"provider '{provider}' is not available for BYO — use "
        "anthropic | openai | google | grok | openai-compatible (local is self-host only)."
    )
