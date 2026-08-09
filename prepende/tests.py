"""Run the repository test suite with ``python -m prepende.tests``."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path


def run() -> None:
    root = Path(__file__).resolve().parents[1]
    suite = unittest.defaultTestLoader.discover(str(root / "tests"))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    run()
