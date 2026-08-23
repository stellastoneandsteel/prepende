"""Small compatibility CLI for the product-neutral cockpit projection."""

from __future__ import annotations

import argparse
import json

from .cockpit import (
    PREPENDE_SCOPE,
    cockpit_manifest,
    receipt_stage_view,
    render_manifest_text,
    render_stage_view_text,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="prepende-brain")
    sub = parser.add_subparsers(dest="command", required=True)
    cockpit = sub.add_parser("cockpit")
    cockpit.add_argument("--scope", default=PREPENDE_SCOPE)
    cockpit.add_argument("--receipt", default="")
    cockpit.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.receipt:
        with open(args.receipt, encoding="utf-8") as stream:
            payload = json.load(stream)
        value = receipt_stage_view(payload)
        print(json.dumps(value, indent=2) if args.json else render_stage_view_text(value))
    else:
        value = cockpit_manifest(scope=args.scope)
        print(json.dumps(value, indent=2) if args.json else render_manifest_text(value))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
