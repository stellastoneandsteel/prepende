"""Canonical model IDs and friendly aliases used by Prepende routing.

The provider remains independently selectable; these are model IDs, not new
providers. Explicit ``MODEL_NAME`` values continue to win over defaults.
"""

from __future__ import annotations

from typing import Final


OPENAI_SOL: Final[str] = "gpt-5.6-sol"
OPENAI_TERRA: Final[str] = "gpt-5.6-terra"
OPENAI_LUNA: Final[str] = "gpt-5.6-luna"
OPENAI_LEGACY: Final[str] = "gpt-5.5"
ANTHROPIC_FABLE: Final[str] = "claude-fable-5"
ANTHROPIC_OPUS_48: Final[str] = "claude-opus-4-8"
ANTHROPIC_SONNET_5: Final[str] = "claude-sonnet-5"

DEFAULT_MODEL_BY_PROVIDER: Final[dict[str, str]] = {
    "openai": OPENAI_SOL,
    "anthropic": ANTHROPIC_FABLE,
    "cli-codex": OPENAI_SOL,
    "codex-cli": OPENAI_SOL,
    "cli-claude": ANTHROPIC_FABLE,
    "claude-cli": ANTHROPIC_FABLE,
}

_ALIASES: Final[dict[str, tuple[str, str]]] = {
    "sol": ("openai", OPENAI_SOL),
    "gpt-5.6-sol": ("openai", OPENAI_SOL),
    "terra": ("openai", OPENAI_TERRA),
    "gpt-5.6-terra": ("openai", OPENAI_TERRA),
    "luna": ("openai", OPENAI_LUNA),
    "gpt-5.6-luna": ("openai", OPENAI_LUNA),
    "fable": ("anthropic", ANTHROPIC_FABLE),
    "fable-5": ("anthropic", ANTHROPIC_FABLE),
    "claude-fable-5": ("anthropic", ANTHROPIC_FABLE),
    "opus-4.8": ("anthropic", ANTHROPIC_OPUS_48),
    "opus-4-8": ("anthropic", ANTHROPIC_OPUS_48),
    "claude-opus-4-8": ("anthropic", ANTHROPIC_OPUS_48),
    "sonnet-5": ("anthropic", ANTHROPIC_SONNET_5),
    "claude-sonnet-5": ("anthropic", ANTHROPIC_SONNET_5),
}

# Ordered availability chains. The first ID is preferred; the remainder are
# tried only when the provider rejects the selected model as unavailable or
# rate-limited. These are deliberately provider-local: Engram never silently
# crosses an API-key or subscription boundary.
_MODEL_ORDER_BY_PROVIDER: Final[dict[str, tuple[str, ...]]] = {
    "openai": (OPENAI_SOL, OPENAI_TERRA, OPENAI_LUNA, OPENAI_LEGACY),
    "anthropic": (ANTHROPIC_FABLE, ANTHROPIC_OPUS_48, ANTHROPIC_SONNET_5),
}


def model_fallbacks(provider: str, requested: str | None = None) -> tuple[str, ...]:
    """Return the remaining provider-local models after ``requested``."""

    provider_id = (provider or "").strip().lower()
    provider_family = {
        "cli-codex": "openai",
        "codex-cli": "openai",
        "cli-claude": "anthropic",
        "claude-cli": "anthropic",
    }.get(provider_id, provider_id)
    order = _MODEL_ORDER_BY_PROVIDER.get(provider_family, ())
    current = resolve_model_id(provider_id, requested)
    if not current:
        return ()
    try:
        index = order.index(current)
    except ValueError:
        # Explicit custom model IDs are never silently replaced.
        return ()
    return order[index + 1 :]


def resolve_model_id(provider: str, requested: str | None = None) -> str:
    """Resolve a friendly alias or provider default to an exact model ID."""

    provider_id = (provider or "").strip().lower()
    provider_family = {
        "cli-codex": "openai",
        "codex-cli": "openai",
        "cli-claude": "anthropic",
        "claude-cli": "anthropic",
    }.get(provider_id, provider_id)
    requested_id = (requested or "").strip()
    if not requested_id:
        return DEFAULT_MODEL_BY_PROVIDER.get(provider_id, "")
    alias = _ALIASES.get(requested_id.lower())
    if alias is None:
        return requested_id
    expected_provider, model_id = alias
    if provider_family not in {"openai", "anthropic"}:
        # Echo, local, and generic OpenAI-compatible endpoints may legitimately
        # have a model literally named "terra" or "fable". Leave those values
        # untouched instead of pretending they are first-party IDs.
        return requested_id
    if expected_provider != provider_family:
        raise ValueError(
            f"model alias '{requested_id}' belongs to provider '{expected_provider}', "
            f"not '{provider_id}'"
        )
    return model_id


def model_aliases() -> dict[str, str]:
    """Return a redacted, JSON-safe alias map for setup/status surfaces."""

    return {alias: model_id for alias, (_, model_id) in _ALIASES.items()}
