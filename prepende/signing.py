"""Optional Ed25519 verification for anchors and independent resolutions."""
from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from .canonical import canonical_bytes


_TRUST_TIMESTAMP_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z$"
)


class SignatureUnavailable(RuntimeError):
    """Raised when signature support was requested without the optional dependency."""


class SignatureError(ValueError):
    """Raised for malformed or invalid signatures and trust records."""


def _ed25519():
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
            Ed25519PublicKey,
        )
    except ImportError as exc:  # pragma: no cover - exercised in base-install smoke
        raise SignatureUnavailable(
            "Ed25519 verification requires the 'signatures' package extra"
        ) from exc
    return Ed25519PrivateKey, Ed25519PublicKey, InvalidSignature


def _parse_time(value: str | None) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str) or not _TRUST_TIMESTAMP_RE.fullmatch(value):
        raise SignatureError("trust-store timestamps must be canonical RFC3339 UTC")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SignatureError("trust-store timestamps must be RFC3339 UTC") from exc
    if parsed.tzinfo is None:
        raise SignatureError("trust-store timestamps must include a timezone")
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class TrustedKey:
    key_id: str
    public_key: str
    algorithm: str = "ed25519"
    valid_from: str | None = None
    valid_until: str | None = None
    revoked_at: str | None = None

    @staticmethod
    def from_value(key_id: str, value: Any) -> "TrustedKey":
        if isinstance(value, str):
            return TrustedKey(key_id=key_id, public_key=value)
        if not isinstance(value, Mapping):
            raise SignatureError("trusted key must be a base64 string or object")
        public_key = value.get("public_key")
        algorithm = value.get("algorithm", "ed25519")
        if not isinstance(public_key, str) or not public_key:
            raise SignatureError("trusted key public_key must be a non-empty base64 string")
        if not isinstance(algorithm, str):
            raise SignatureError("trusted key algorithm must be a string")
        for field_name in ("valid_from", "valid_until", "revoked_at"):
            field_value = value.get(field_name)
            if field_value is not None and not isinstance(field_value, str):
                raise SignatureError(f"trusted key {field_name} must be a timestamp string")
        return TrustedKey(
            key_id=key_id,
            public_key=public_key,
            algorithm=algorithm,
            valid_from=value.get("valid_from"),
            valid_until=value.get("valid_until"),
            revoked_at=value.get("revoked_at"),
        )

    def valid_at(self, when: str) -> bool:
        instant = _parse_time(when)
        assert instant is not None
        starts = _parse_time(self.valid_from)
        ends = _parse_time(self.valid_until)
        revoked = _parse_time(self.revoked_at)
        return not (
            (starts is not None and instant < starts)
            or (ends is not None and instant > ends)
            or revoked is not None
        )


def verify_detached(statement: Any, signature_b64: str, key: TrustedKey, *, at: str) -> None:
    if key.algorithm != "ed25519":
        raise SignatureError(f"unsupported signature algorithm: {key.algorithm}")
    if not key.valid_at(at):
        raise SignatureError(f"key {key.key_id} was not valid at {at}")
    _, PublicKey, InvalidSignature = _ed25519()
    try:
        public_bytes = base64.b64decode(key.public_key, validate=True)
        signature = base64.b64decode(signature_b64, validate=True)
        public = PublicKey.from_public_bytes(public_bytes)
        public.verify(signature, canonical_bytes(statement))
    except (ValueError, InvalidSignature) as exc:
        raise SignatureError(f"invalid signature for key {key.key_id}") from exc


def sign_detached(statement: Any, private_key_b64: str) -> str:
    """Sign a statement. Intended for test fixtures and external authority tools."""
    PrivateKey, _, _ = _ed25519()
    try:
        raw = base64.b64decode(private_key_b64, validate=True)
        private = PrivateKey.from_private_bytes(raw)
    except ValueError as exc:
        raise SignatureError("invalid Ed25519 private key") from exc
    return base64.b64encode(private.sign(canonical_bytes(statement))).decode("ascii")
