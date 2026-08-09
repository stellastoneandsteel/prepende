"""Canonical, domain-separated encoding for Prepende Protocol v2.

Protocol rows deliberately reject binary floating-point values. Hashed numbers are
represented as canonical decimal strings so independent implementations do not depend
on a language's float printer.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from decimal import Decimal, InvalidOperation
from typing import Any


PROTOCOL = "prepende/2"
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class CanonicalizationError(ValueError):
    """Raised when a value cannot be represented by the v2 canonical profile."""


def decimal_string(value: Any, *, minimum: Decimal | None = None,
                   maximum: Decimal | None = None) -> str:
    """Return a finite, non-exponent decimal string with insignificant zeros removed."""
    if isinstance(value, bool):
        raise CanonicalizationError("boolean is not a numeric value")
    if isinstance(value, float) and not math.isfinite(value):
        raise CanonicalizationError("numeric values must be finite")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise CanonicalizationError("invalid decimal value") from exc
    if not number.is_finite():
        raise CanonicalizationError("numeric values must be finite")
    if minimum is not None and number < minimum:
        raise CanonicalizationError(f"numeric value must be >= {minimum}")
    if maximum is not None and number > maximum:
        raise CanonicalizationError(f"numeric value must be <= {maximum}")
    if number == 0:
        return "0"
    text = format(number.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _normalize(value: Any, path: str = "$") -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float) or isinstance(value, Decimal):
        raise CanonicalizationError(
            f"{path}: floats/decimals are forbidden in hashed values; use decimal strings"
        )
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, list):
        return [_normalize(item, f"{path}[{index}]") for index, item in enumerate(value)]
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise CanonicalizationError(f"{path}: object keys must be strings")
            normalized_key = unicodedata.normalize("NFC", key)
            if normalized_key in out:
                raise CanonicalizationError(f"{path}: duplicate normalized key {normalized_key!r}")
            out[normalized_key] = _normalize(item, f"{path}.{normalized_key}")
        return out
    raise CanonicalizationError(f"{path}: unsupported value type {type(value).__name__}")


def canonical_bytes(value: Any) -> bytes:
    normalized = _normalize(value)
    return json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def digest(domain: str, value: Any) -> str:
    if not domain or "\x00" in domain:
        raise CanonicalizationError("hash domain must be a non-empty string without NUL")
    payload = b"prepende-v2\x00" + domain.encode("ascii") + b"\x00" + canonical_bytes(value)
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def file_digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def require_digest(value: Any, label: str = "digest") -> str:
    text = str(value or "")
    if not _DIGEST_RE.fullmatch(text):
        raise CanonicalizationError(f"{label} must be sha256:<64 lowercase hex characters>")
    return text
