"""Canonical Prepende Actions API module.

The implementation remains in ``interface.engram_api`` during the compatibility
window so existing imports keep identical scoped behavior.
"""

from __future__ import annotations

from interface.engram_api import *  # noqa: F401,F403
from interface.engram_api import serve


if __name__ == "__main__":
    serve()
