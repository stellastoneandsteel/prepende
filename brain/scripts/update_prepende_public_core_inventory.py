#!/usr/bin/env python3
"""Render or check the exact reviewed Prepende public-core inventory."""

from __future__ import annotations

from pathlib import Path

import update_prepende_export_inventory as inventory


inventory.POLICY_PATH = inventory.ROOT / "prepende-public-core-manifest.json"
inventory.INVENTORY_PATH = inventory.ROOT / "prepende-public-core-reviewed-inventory.json"
inventory.OVERRIDES = {
    **inventory.OVERRIDES,
    "README.md": "distribution/prepende-public-core/README.md",
    "package.json": "distribution/prepende-public-core/package.json",
    "pyproject.toml": "distribution/prepende-public-core/pyproject.toml",
}


if __name__ == "__main__":
    raise SystemExit(inventory.main())
