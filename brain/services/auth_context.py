"""Authenticated user context for backend routes.

The v1 route boundary accepts a signed bearer token and returns a server-side
user context. A real Firebase/Supabase verifier can replace this module without
changing route or storage code.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from typing import Any, Mapping

from prepende_brain.env import brand_env


class AuthError(Exception):
    def __init__(self, message: str, status: int = 401) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class AuthContext:
    user_id: str
    email: str = ""


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _b64decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def _auth_secret() -> str:
    secret = brand_env("AUTH_SECRET").strip()
    if not secret:
        raise AuthError("auth is not configured", 500)
    return secret


def _sign(payload: str, secret: str) -> str:
    digest = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest()
    return _b64encode(digest)


def mint_auth_token(user_id: str, *, email: str = "", ttl_seconds: int = 3600, secret: str | None = None) -> str:
    """Create a signed bearer token for local tests and trusted backend tooling."""
    if not user_id.strip():
        raise ValueError("user_id is required")
    auth_secret = secret or _auth_secret()
    payload = {
        "sub": user_id.strip(),
        "email": email.strip(),
        "exp": int(time.time()) + ttl_seconds,
    }
    encoded = _b64encode(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
    return f"engram.{encoded}.{_sign(encoded, auth_secret)}"


def require_auth(headers: Mapping[str, Any]) -> AuthContext:
    raw = str(headers.get("Authorization") or headers.get("authorization") or "").strip()
    if not raw.startswith("Bearer "):
        raise AuthError("authenticated user required")

    token = raw.removeprefix("Bearer ").strip()
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != "engram":
        raise AuthError("invalid bearer token")

    secret = _auth_secret()
    payload, signature = parts[1], parts[2]
    expected = _sign(payload, secret)
    if not hmac.compare_digest(signature, expected):
        raise AuthError("invalid bearer token")

    try:
        data = json.loads(_b64decode(payload))
    except Exception as exc:
        raise AuthError(f"invalid bearer token: {type(exc).__name__}") from exc

    exp = int(data.get("exp") or 0)
    if exp and exp < int(time.time()):
        raise AuthError("bearer token expired")

    user_id = str(data.get("sub") or "").strip()
    if not user_id:
        raise AuthError("bearer token missing subject")
    return AuthContext(user_id=user_id, email=str(data.get("email") or "").strip())
