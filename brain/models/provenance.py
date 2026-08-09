"""Truthful, secret-free provenance for model-backed kernel receipts."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class ModelProvenance:
    provider: str
    auth_lane: str
    requested_model: str
    resolved_model: str | None
    fallback_used: bool
    resolved_provider: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def model_provenance(gateway: Any) -> ModelProvenance:
    """Describe the selected adapter without guessing or exposing credentials."""

    provider = str(getattr(gateway, "name", gateway.__class__.__name__) or "unknown").lower()
    requested = str(
        getattr(gateway, "requested_model", "")
        or getattr(gateway, "model", "")
        or "provider-default"
    )

    explicit_resolved = getattr(gateway, "resolved_model", None)
    fallback_used = bool(explicit_resolved and explicit_resolved != requested)
    if provider in {"auto", "best-available"}:
        return ModelProvenance(
            provider,
            "multi-provider",
            requested,
            explicit_resolved,
            fallback_used,
            getattr(gateway, "resolved_provider", None),
        )
    if provider.endswith("-sub") or gateway.__class__.__name__ == "CliGateway":
        return ModelProvenance(
            provider, "subscription", requested, explicit_resolved, fallback_used
        )
    if provider == "echo":
        return ModelProvenance(provider, "none", requested, "echo", False)
    if provider == "local":
        return ModelProvenance(provider, "local", requested, explicit_resolved, fallback_used)
    if provider in {"anthropic", "openai", "google", "grok", "xai", "openai-compatible"}:
        return ModelProvenance(provider, "api_key", requested, explicit_resolved, fallback_used)
    return ModelProvenance(provider, "unknown", requested, explicit_resolved, fallback_used)
