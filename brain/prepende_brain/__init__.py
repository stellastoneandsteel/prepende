"""Product-neutral runtime helpers for the Prepende brain."""

from .cockpit import (
    PREPENDE_SCOPE,
    article_quality_receipt,
    cockpit_manifest,
    receipt_stage_view,
)

__version__ = "0.1.0rc1"

__all__ = [
    "PREPENDE_SCOPE",
    "article_quality_receipt",
    "cockpit_manifest",
    "receipt_stage_view",
]
