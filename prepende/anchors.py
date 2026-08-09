"""Provider-neutral interface for externally signed checkpoint statements."""
from __future__ import annotations

from typing import Any, Protocol

from .canonical import PROTOCOL


class AnchorProvider(Protocol):
    """An external authority that returns a detached receipt for a statement."""

    provider_id: str
    key_id: str

    def anchor(self, request: dict[str, Any]) -> dict[str, Any]:
        """Return an authority-timestamped ``statement`` and detached ``signature``."""
        ...


class ResolutionSigner(Protocol):
    """An external resolver that signs the ledger-prepared resolution statement."""

    key_id: str

    def sign(self, statement: dict[str, Any]) -> str:
        """Return a base64 Ed25519 signature over canonical statement bytes."""
        ...


def build_anchor_statement(request: dict[str, Any], *, key_id: str,
                           anchored_at: str) -> dict[str, Any]:
    """Authority-side helper that adds the authority's identity and clock value."""
    expected = {
        "protocol", "stream_id", "checkpoint_id", "checkpoint_row_hash",
        "covered_through", "covered_head", "row_count",
    }
    if set(request) != expected or request.get("protocol") != PROTOCOL:
        raise ValueError("invalid Prepende anchor request")
    if not key_id or not anchored_at:
        raise ValueError("anchor authority key_id and anchored_at are required")
    return {**request, "anchored_at": anchored_at, "key_id": key_id}
