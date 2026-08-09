"""Shared HTTP helper for model adapters: POST JSON with retries-and-ceiling.

Transient failures (timeouts, 429, 5xx) retry with backoff up to a ceiling;
genuine client errors (401/403/404) fail fast — no point retrying a bad key or
model name. Stdlib only.
"""

from __future__ import annotations

import time
import urllib.error
import urllib.request


def http_error_text(exc: Exception) -> str:
    """Return a bounded provider error body captured by :func:`post_json`.

    ``urllib.error.HTTPError`` is also a file-like response. Reading it later is
    unreliable because retry/error handling may already have consumed the body,
    so ``post_json`` captures the bytes once on the exception itself. Model
    adapters use this only to classify an error; callers never receive provider
    payloads or credentials in public API responses.
    """

    raw = getattr(exc, "prepende_body", b"")
    if isinstance(raw, bytes):
        return raw[:8192].decode("utf-8", errors="replace")
    return str(raw or "")[:8192]


def post_json(url: str, payload: bytes, headers: dict[str, str], timeout: int = 120, retries: int = 2) -> bytes:
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, data=payload, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            try:
                e.prepende_body = e.read(8192)
            except Exception:
                e.prepende_body = b""
            # 4xx (bad key/model/request) are not transient — fail fast.
            if e.code not in (429, 500, 502, 503, 504):
                raise
            last = e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = e  # timeouts, DNS, connection resets — transient
        if attempt < retries:
            time.sleep(1.5 * (attempt + 1))
    raise last if last else RuntimeError("post_json failed")


def get_json(url: str, headers: dict[str, str] | None = None, timeout: int = 2, retries: int = 0) -> tuple[int | None, bool]:
    """A cheap reachability GET sharing post_json's error taxonomy.

    Returns (status_code, reachable):
      - (200, True)  — server responded OK
      - (401/404…, True) — server is UP but the request was refused (key/path) — still "reachable"
      - (None, False) — socket/DNS/timeout failure — the server is not there
    Used by the context detector to tell "no local model server" from
    "server up but unauthed/no model". Never raises for the unreachable case.
    """
    headers = headers or {}
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers, method="GET")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.getcode(), True
        except urllib.error.HTTPError as e:
            return e.code, True  # the server answered (even 4xx) — it IS reachable
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = e  # no socket / DNS / timeout — not reachable
        if attempt < retries:
            time.sleep(0.5 * (attempt + 1))
    return None, False
