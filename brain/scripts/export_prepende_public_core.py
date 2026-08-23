#!/usr/bin/env python3
"""Export the reviewed product-neutral Prepende core without private history."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import export_prepende_clone as exporter


exporter.POLICY_PATH = "prepende-public-core-manifest.json"
exporter.INVENTORY_PATH = "prepende-public-core-reviewed-inventory.json"
exporter.OVERRIDES = {
    **exporter.OVERRIDES,
    "README.md": "distribution/prepende-public-core/README.md",
    "package.json": "distribution/prepende-public-core/package.json",
    "pyproject.toml": "distribution/prepende-public-core/pyproject.toml",
}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export the reviewed history-free Prepende public brain core"
    )
    parser.add_argument("--output", required=True, help="new directory outside this checkout")
    parser.add_argument("--json", action="store_true", help="print the export receipt as JSON")
    args = parser.parse_args()
    try:
        receipt = exporter.export_index(Path(args.output))
    except exporter.ExportRefusal as exc:
        parser.error(str(exc))
    if args.json:
        print(json.dumps(receipt, indent=2, sort_keys=True))
    else:
        print(
            f"Prepende public core exported to {receipt['destination']} "
            f"({receipt['fileCount']} reviewed text files; no private history or state)"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
