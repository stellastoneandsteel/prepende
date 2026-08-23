#!/usr/bin/env python3
"""Owner-only reviewed knowledge bundle importer.

This host CLI is the only supported import path for product graph bundles. It
does not accept an MCP token and is not registered as a runtime tool.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from knowledge.reviewed_bundle import ReviewedBundleError, import_reviewed_bundle  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Import one byte-bound, owner-approved knowledge bundle."
    )
    parser.add_argument("--bundle", required=True)
    parser.add_argument("--approval-manifest", required=True)
    parser.add_argument("--vault-base", required=True)
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--scope", default="", help="physical vault scope (default: workspace)")
    parser.add_argument(
        "--imported-at", default="",
        help="fixed operation timestamp for controlled output (not persisted)",
    )
    return parser


async def _run(args: argparse.Namespace) -> dict:
    return await import_reviewed_bundle(
        bundle_path=args.bundle,
        approval_manifest_path=args.approval_manifest,
        vault_base=args.vault_base,
        tenant=args.tenant,
        workspace=args.workspace,
        scope=args.scope,
        imported_at=args.imported_at,
    )


def main() -> None:
    parser = _parser()
    args = parser.parse_args()
    try:
        receipt = asyncio.run(_run(args))
    except ReviewedBundleError as exc:
        parser.exit(2, f"reviewed bundle import refused: {exc}\n")
    print(json.dumps(receipt, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
