"""Command-line contract for clone bootstrap and knowledge projections."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

from kernel.core.config import Config
from knowledge.bootstrap import VaultBootstrapError, initialize_vault
from knowledge.operations import backfill, print_receipt, rebuild, search, status
from prepende_brain.private_fs import secure_directory


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="prepende knowledge")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="initialize a sanitized private vault")
    init.add_argument(
        "--data-dir",
        default=os.environ.get("PREPENDE_DATA_DIR", "./prepende-data/default"),
        help="private runtime directory; the vault is created below it",
    )
    init.add_argument("--json", action="store_true")

    state = sub.add_parser("status", help="report RAG and Graphify readiness")
    state.add_argument("--json", action="store_true")

    rebuild_cmd = sub.add_parser("rebuild", help="rebuild RAG from canonical Markdown")
    rebuild_cmd.add_argument("--json", action="store_true")

    fill = sub.add_parser("backfill", help="converge every configured embedding")
    fill.add_argument("--max-rounds", type=int, default=100)
    fill.add_argument("--json", action="store_true")

    find = sub.add_parser("search", help="search the active vault projection")
    find.add_argument("query")
    find.add_argument("--limit", type=int, default=8)
    find.add_argument("--json", action="store_true")

    graph = sub.add_parser(
        "graphify-finalize",
        help="bind an existing semantic graph to the active owner vault",
    )
    graph.add_argument("--root", default="", help="corpus root (default: VAULT_PATH)")
    graph.add_argument(
        "--out",
        default="./graphify-out/knowledge",
        help="owner knowledge Graphify output directory",
    )
    graph.add_argument("--json", action="store_true")
    return parser


def _print_init(receipt: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(receipt, indent=2))
        return
    print(f"Prepende vault initialized: {receipt['vaultPath']}")
    print(f"  created: {len(receipt['created'])} template file(s)")
    print("  source data copied: no")
    print(f"  set VAULT_PATH={receipt['vaultPath']}")
    print("  next: prepende knowledge rebuild")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "init":
        data_dir = Path(args.data_dir).expanduser().resolve(strict=False)
        vault = data_dir / "vault"
        try:
            secure_directory(data_dir, repair_existing=True)
            receipt = initialize_vault(vault)
        except (RuntimeError, VaultBootstrapError) as exc:
            payload = {"ok": False, "operation": "init", "error": str(exc)}
            if args.json:
                print(json.dumps(payload, indent=2))
            else:
                print(f"prepende: {exc}")
            return 1
        payload = {
            **receipt,
            "operation": "init",
            "dataDir": str(data_dir),
            "vaultPath": str(vault),
        }
        _print_init(payload, args.json)
        return 0

    if args.command == "graphify-finalize":
        root = args.root or Config().vault
        output = Path(args.out).expanduser().resolve(strict=False)
        marker = output / ".graphify_python"
        interpreter = sys.executable
        if marker.is_file():
            candidate = marker.read_text(encoding="utf-8").strip()
            if candidate and Path(candidate).is_file():
                interpreter = candidate
        command = [
            interpreter,
            str(Path(__file__).resolve().parents[1] / "scripts" / "refresh_graphify.py"),
            "--root", root,
            "--out", str(output),
            "--manifest-only",
        ]
        if args.json:
            command.append("--json")
        return subprocess.run(command).returncode

    if args.command == "status":
        result = status()
    elif args.command == "rebuild":
        result = asyncio.run(rebuild())
    elif args.command == "backfill":
        result = asyncio.run(backfill(max_rounds=max(1, args.max_rounds)))
    elif args.command == "search":
        result = asyncio.run(search(args.query, k=args.limit))
    else:  # argparse enforces this; retained for type checkers.
        return 2
    print_receipt(result, as_json=args.json)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
