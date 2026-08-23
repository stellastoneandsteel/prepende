"""Encrypted secret vault for per-tenant BYO-brain custody (Phase 1).

See docs/BYO-BRAIN-DESIGN.md. This is the one place tenant model API keys (and
external-brain tokens) are encrypted/decrypted. It exists to make custody
STRUCTURAL, not a promise:

  - AEAD (AES-256-GCM): ciphertext is authenticated; tampering fails to decrypt.
  - The master key lives ONLY in host env (PREPENDE_KEY_VAULT_MASTER_KEY), never in
    the repo or the database. A DB dump (or a BYPASSRLS service_role token) yields
    only opaque base64 — useless without the separately-held master key.
  - Associated data binds each ciphertext to (scope, purpose): a row physically
    copied into another tenant's scope will NOT decrypt.
  - FAIL-CLOSED: no master key, or no `cryptography` lib, => the vault is
    unavailable and raises — it NEVER returns plaintext or falls back to a
    shared/plaintext path.
  - Key-safety helpers (fingerprint / is_key_shaped / scrub) so no surface ever
    serializes or logs a real key; a release-gate smoke (tests/smoke_keyvault.py)
    asserts it.

Optional dependency: `cryptography` (hosted-only; see requirements-api.txt). The
stdlib-first core never imports it unless the vault is actually used.
"""

from __future__ import annotations

import base64
import hashlib
import os
import re

from prepende_brain.env import brand_env

_MASTER_ENV = "PREPENDE_KEY_VAULT_MASTER_KEY"
_NONCE_LEN = 12  # AES-GCM standard nonce

# Key-shaped strings, for the no-leak gate + log scrubbing. Common provider
# prefixes plus a generic high-entropy run. Deliberately broad for the gate.
_KEY_RE = re.compile(
    r"(sk-ant-[A-Za-z0-9_-]{12,}"
    r"|sk-[A-Za-z0-9_-]{16,}"
    r"|re_[A-Za-z0-9_-]{12,}"
    r"|AIza[A-Za-z0-9_-]{20,}"
    r"|xoxb-[A-Za-z0-9-]{10,}"
    r"|[A-Za-z0-9_-]{48,})"
)


class VaultUnavailable(RuntimeError):
    """The vault cannot operate (no master key, or no crypto lib). Fail-closed."""


class VaultDecryptError(RuntimeError):
    """Ciphertext could not be authenticated/decrypted (wrong scope/purpose,
    tampering, or wrong master key). Never leaks why beyond the class."""


def _master_key() -> bytes:
    raw = brand_env("KEY_VAULT_MASTER_KEY")
    if not raw:
        raise VaultUnavailable(
            f"{_MASTER_ENV} is not set — the key vault is unavailable (fail-closed). "
            "Set a long random value on the host (e.g. `openssl rand -hex 32`)."
        )
    # Derive a 32-byte key from the env value so any sufficiently-random string
    # works; the value itself never leaves host env.
    return hashlib.sha256(raw.encode("utf-8")).digest()


def _aesgcm():
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as e:  # noqa: BLE001 — a broken/mismatched install can raise
        # non-Exception errors (e.g. a pyo3 PanicException from a bad native build);
        # either way the vault must fail CLOSED as unavailable, never crash callers.
        raise VaultUnavailable(
            "the 'cryptography' package is not installed or not usable — key vault "
            "unavailable (fail-closed; install it on the hosted API per requirements-api.txt)."
        ) from e
    return AESGCM(_master_key())


def available() -> bool:
    """True only if the vault can actually seal/unseal (master key + lib present).
    Never raises — callers gate BYO key-write paths on this."""
    try:
        _aesgcm()
        return True
    except VaultUnavailable:
        return False


def _aad(scope: str, purpose: str) -> bytes:
    return f"{(scope or '').strip()}|{(purpose or '').strip()}".encode("utf-8")


def seal(scope: str, purpose: str, plaintext: str) -> str:
    """Encrypt `plaintext` for (scope, purpose) -> base64(nonce + ciphertext).
    Raises VaultUnavailable if the vault isn't operational."""
    if not (scope or "").strip() or not (purpose or "").strip():
        raise ValueError("seal requires a non-empty scope and purpose")
    if plaintext is None:
        raise ValueError("seal requires plaintext")
    aes = _aesgcm()
    nonce = os.urandom(_NONCE_LEN)
    ct = aes.encrypt(nonce, plaintext.encode("utf-8"), _aad(scope, purpose))
    return base64.b64encode(nonce + ct).decode("ascii")


def unseal(scope: str, purpose: str, ciphertext_b64: str) -> str:
    """Decrypt for (scope, purpose). Raises VaultDecryptError on any mismatch
    (wrong scope/purpose AAD, tamper, wrong master key) — never says which."""
    aes = _aesgcm()
    try:
        blob = base64.b64decode(ciphertext_b64)
        nonce, ct = blob[:_NONCE_LEN], blob[_NONCE_LEN:]
        return aes.decrypt(nonce, ct, _aad(scope, purpose)).decode("utf-8")
    except VaultUnavailable:
        raise
    except Exception as e:  # noqa: BLE001 — class only, never the value/reason detail
        raise VaultDecryptError("secret could not be decrypted") from e


def fingerprint(plaintext: str, provider: str = "") -> str:
    """A safe, displayable hint — provider + last 4 only. NEVER the key."""
    s = plaintext or ""
    last4 = s[-4:] if len(s) >= 4 else "••••"
    p = (provider or "").strip()
    return (p + " " if p else "") + "••••" + last4


def is_key_shaped(s: str) -> bool:
    """Does this string contain something that looks like an API key/token?
    Used by the release gate to assert no surface leaks a key."""
    return bool(_KEY_RE.search(s or ""))


def scrub(s: str) -> str:
    """Redact key-shaped substrings for safe logging."""
    return _KEY_RE.sub("•••redacted•••", s or "")
