"""Per-tenant model routing for BYO-brain (Phase 2). See docs/BYO-BRAIN-DESIGN.md.

A tenant can run on THEIR own model (their key, their cost). This resolves the
GENERATION gateway for a request's tenant scope:
  - shared / unconfigured  -> the shared brain gateway (unchanged baseline)
  - byo_model              -> a per-tenant gateway built from their decrypted key

INVARIANTS (structural):
  - EMBEDDING is never touched here. This module only returns a GENERATION
    gateway; the shared memory store keeps its build_brain()-time embedder, so
    recall can't corrupt across the shared index.
  - FAIL-LOUD: a BYO tenant with a missing/unreadable/disallowed key raises
    TenantBrainError. Callers MUST NOT fall back to the shared (paid) model — that
    would silently bill the operator for a tenant's misconfiguration.
  - Keys are only ever handled as ciphertext until the moment of build, decrypted
    via kernel/core/keyvault.py (master key in host env). Never logged/returned.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from kernel.core import keyvault
from models.factory import build_gateway_from

# Providers a HOSTED tenant may bring. NO local/cli: a hosted brain can't reach a
# laptop model, and cli-* is subscription-personal (vendor ToS).
_HOSTED_BYO_PROVIDERS = {"anthropic", "openai", "google", "grok", "xai", "openai-compatible"}
BYO_SECRET_PURPOSE = "byo:model"
_CACHE_TTL_S = 60.0
_cache: dict[str, Any] = {}  # scope -> (expires_at, fingerprint, gateway, source)


class TenantBrainError(RuntimeError):
    """A tenant's BYO config is set but unusable. Raised so chat fails loud."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class ModelChoice:
    mode: str = "shared"   # shared | byo_model | external_brain
    provider: str = ""
    model: str = ""
    base: str = ""


def provider_allowed_hosted(provider: str) -> bool:
    return (provider or "").lower() in _HOSTED_BYO_PROVIDERS


def brain_source(choice: ModelChoice) -> str:
    mode = (choice.mode or "shared").lower()
    if mode in ("", "shared"):
        return "shared"
    if mode == "byo_model":
        return "byo:" + (choice.provider or "?")
    return mode


def gateway_for_choice(choice: ModelChoice, secret_plaintext: str | None, shared_gateway: Any) -> Any:
    """PURE: pick the generation gateway for a choice. Never touches embeddings or
    the DB. Raises TenantBrainError on anything unusable (callers do NOT fall back)."""
    mode = (choice.mode or "shared").lower()
    if mode in ("", "shared"):
        return shared_gateway
    if mode == "byo_model":
        if not provider_allowed_hosted(choice.provider):
            raise TenantBrainError("provider_not_allowed",
                                   f"'{choice.provider}' is not a hosted BYO provider")
        if not secret_plaintext:
            raise TenantBrainError("byo_key_missing", "no API key on file for this tenant")
        try:
            return build_gateway_from(choice.provider, choice.model, secret_plaintext, choice.base)
        except Exception as e:  # noqa: BLE001 — class only, never the key
            raise TenantBrainError("byo_build_failed", type(e).__name__) from e
    if mode == "external_brain":
        raise TenantBrainError("external_brain_unavailable", "external-brain mode ships in a later phase")
    raise TenantBrainError("unknown_mode", mode)


async def resolve_tenant_gateway(scope: str, shared_gateway: Any, *, store=None, secrets=None) -> tuple[Any, str]:
    """DB-backed entry: returns (gateway, source). shared/unconfigured -> the shared
    gateway; byo_model -> a per-tenant gateway from the decrypted key. Raises
    TenantBrainError (fail-loud) if a BYO tenant's key is missing/unreadable. 60s
    cache keyed by (scope, key fingerprint) so a rotated key invalidates."""
    scope = (scope or "").strip()
    if not scope:
        return shared_gateway, "shared"

    if store is None or secrets is None:
        from kernel.core.config import Config
        cfg = Config()
        dsn = cfg.database_url
        # Honor the MEMORY_BACKEND guard: when the deployment pins sqlite (local dev),
        # never auto-connect to the postgres DSN — local runs must not reach the prod
        # brain just because .env carries DATABASE_URL. Postgres/auto stays DB-backed.
        if not dsn or cfg.memory_backend == "sqlite":
            return shared_gateway, "shared"  # no durable store -> shared baseline
        from memory.tenant_brain_store import TenantBrainStore
        from memory.secret_store import SecretStore
        store = store or TenantBrainStore(dsn)
        secrets = secrets or SecretStore(dsn)

    try:
        row = await store.get(scope)
    except RuntimeError as e:
        # The per-tenant brain table isn't provisioned (migration 027 not applied):
        # there can be no BYO tenants, so degrade to the shared gateway instead of
        # 500-ing EVERY chat on this deployment. A configured BYO tenant with a
        # broken key still fails loud below — that path requires a row, which
        # requires the table to exist. Only this "feature-absent" read degrades.
        if "engram_tenant_brain is missing" in str(e):
            return shared_gateway, "shared"
        raise
    if not row or (row.get("mode") or "shared") == "shared":
        return shared_gateway, "shared"

    choice = ModelChoice(
        mode=row.get("mode") or "shared",
        provider=row.get("provider") or "",
        model=row.get("model_id") or "",
        base=row.get("base_url") or "",
    )
    purpose = row.get("secret_purpose") or BYO_SECRET_PURPOSE
    cipher = await secrets.get_cipher(scope, purpose)
    fp = (cipher or "")[-16:]

    cached = _cache.get(scope)
    if cached and cached[0] > time.monotonic() and cached[1] == fp:
        return cached[2], cached[3]

    if choice.mode == "byo_model" and not cipher:
        raise TenantBrainError("byo_key_missing", "no API key stored for this tenant")

    secret = None
    if cipher:
        try:
            secret = keyvault.unseal(scope, purpose, cipher)
        except keyvault.VaultUnavailable as e:
            raise TenantBrainError("vault_unavailable", "key vault not configured on the host") from e
        except keyvault.VaultDecryptError as e:
            raise TenantBrainError("byo_key_unreadable", "stored key could not be decrypted") from e

    gw = gateway_for_choice(choice, secret, shared_gateway)
    src = brain_source(choice)
    _cache[scope] = (time.monotonic() + _CACHE_TTL_S, fp, gw, src)
    return gw, src


def invalidate(scope: str) -> None:
    """Drop a tenant's cached gateway (call after a config/key change)."""
    _cache.pop((scope or "").strip(), None)
