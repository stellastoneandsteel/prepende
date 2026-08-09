"""Command-line interface for Prepende Protocol v2."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .ledger import Ledger, LedgerIntegrityError, RetrofitError
from .legacy import LegacyLedger
from .plot import reliability_svg
from .report import build_report


def _json(value: str) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError(f"invalid JSON: {exc.msg}") from exc


def _json_file(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("trust store must be a JSON object keyed by key id")
    return value


def _json_list_file(path: str | None) -> list[dict[str, Any]]:
    if not path:
        return []
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError("anchor receipt file must be a JSON array of objects")
    return value


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="prepende",
        description="registered, chained prediction commitments with explicit external trust",
    )
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    ap.add_argument("--ledger", default="ledger-v2.jsonl")
    sub = ap.add_subparsers(dest="cmd", required=True)

    create = sub.add_parser("create", help="create a v2 stream")
    create.add_argument("--stream-id", required=True)
    create.add_argument("--predictor", required=True)
    create.add_argument("--anchor-policy", choices=["required", "optional"], default="required")
    create.add_argument("--legacy-ledger")
    create.add_argument("--legacy-git-commit")

    lock = sub.add_parser("lock", help="append a prediction using the ledger clock")
    lock.add_argument("--predictor", required=True)
    lock.add_argument("--model-version", required=True)
    lock.add_argument("--domain", required=True)
    lock.add_argument("--event-id", required=True)
    lock.add_argument("--question", required=True)
    lock.add_argument("--kind", choices=["probability", "numeric", "categorical"], required=True)
    lock.add_argument("--claim", required=True, type=_json)
    lock.add_argument("--rule", required=True)
    lock.add_argument("--evaluator", required=True, type=_json)
    lock.add_argument("--evaluation", required=True, type=_json)
    lock.add_argument("--due", required=True)
    lock.add_argument("--resolver-policy", required=True, type=_json)
    lock.add_argument("--nonresolution-policy", required=True, type=_json)
    lock.add_argument("--void-policy", type=_json)
    lock.add_argument("--provenance", choices=["forward", "retrospective"], default="forward")

    resolve = sub.add_parser("resolve", help="self-resolve from inline pinned evidence")
    resolve.add_argument("--contract-id", required=True)
    resolve.add_argument("--evidence", required=True, type=_json)
    resolve.add_argument("--note", default="")

    prepare = sub.add_parser("prepare-resolution", help="produce a statement for an authorized signer")
    prepare.add_argument("--contract-id", required=True)
    prepare.add_argument("--evidence", required=True, type=_json)
    prepare.add_argument("--resolver-key-id", required=True)
    prepare.add_argument("--note", default="")

    signed = sub.add_parser("resolve-signed", help="append a signed prepared resolution")
    signed.add_argument("--statement", required=True)
    signed.add_argument("--signature", required=True)

    forfeit = sub.add_parser("forfeit", help="record the locked penalty after a deadline")
    forfeit.add_argument("--contract-id", required=True)
    forfeit.add_argument("--note", default="")

    prepare_void = sub.add_parser("prepare-void", help="produce a locked-reason void statement")
    prepare_void.add_argument("--contract-id", required=True)
    prepare_void.add_argument("--reason-code", required=True)
    prepare_void.add_argument("--resolver-key-id", required=True)
    prepare_void.add_argument("--note", default="")
    void_signed = sub.add_parser("void-signed", help="append an authorized signed void")
    void_signed.add_argument("--statement", required=True)
    void_signed.add_argument("--signature", required=True)

    sub.add_parser("checkpoint", help="append a row-count and chain-head commitment")
    template = sub.add_parser("anchor-request", help="produce a timeless checkpoint request for an authority")
    template.add_argument("--checkpoint-id", required=True)
    anchor = sub.add_parser("add-anchor", help="append an externally signed anchor receipt")
    anchor.add_argument("--statement", required=True)
    anchor.add_argument("--signature", required=True)

    for name in ("verify", "report"):
        command = sub.add_parser(name)
        command.add_argument("--trusted-anchors")
        command.add_argument("--trusted-resolvers")
        command.add_argument("--external-anchor-receipts")
    plot = sub.add_parser("plot")
    plot.add_argument("--out", default="calibration.svg")
    plot.add_argument("--minimum-n", type=int, default=30)
    sub.add_parser("legacy-report", help="read the v1 corpus with explicit legacy limits")
    return ap


def main(argv=None) -> None:
    args = parser().parse_args(argv)
    try:
        if args.cmd == "create":
            if bool(args.legacy_ledger) != bool(args.legacy_git_commit):
                raise ValueError("legacy ledger and git commit must be provided together")
            if args.legacy_ledger:
                ledger = Ledger.from_legacy(
                    args.ledger,
                    legacy_path=args.legacy_ledger,
                    stream_id=args.stream_id,
                    registered_predictor=args.predictor,
                    git_commit=args.legacy_git_commit,
                    anchor_policy=args.anchor_policy,
                )
            else:
                ledger = Ledger.create(
                    args.ledger,
                    stream_id=args.stream_id,
                    registered_predictor=args.predictor,
                    anchor_policy=args.anchor_policy,
                )
            print(json.dumps(ledger.verify().to_dict(), indent=2, sort_keys=True))
            return
        if args.cmd == "legacy-report":
            print(build_report(LegacyLedger(args.ledger)))
            return
        ledger = Ledger(args.ledger)
        if args.cmd == "lock":
            contract = ledger.lock_prediction(
                predictor=args.predictor, model_version=args.model_version,
                domain=args.domain, event_id=args.event_id, question=args.question,
                kind=args.kind, claim=args.claim, resolution_rule=args.rule,
                evaluator=args.evaluator, evaluation=args.evaluation,
                resolution_due_at=args.due, resolver_policy=args.resolver_policy,
                nonresolution_policy=args.nonresolution_policy,
                void_policy=args.void_policy, provenance=args.provenance,
            )
            print(json.dumps({"contract_id": contract.contract_id, "short_id": contract.short_id}, indent=2))
        elif args.cmd == "resolve":
            result = ledger.resolve(args.contract_id, evidence=args.evidence, note=args.note)
            print(json.dumps({"resolution_id": result.resolution_id}, indent=2))
        elif args.cmd == "prepare-resolution":
            print(json.dumps(ledger.prepare_resolution(
                args.contract_id, evidence=args.evidence,
                resolver_key_id=args.resolver_key_id, note=args.note,
            ), indent=2, sort_keys=True))
        elif args.cmd == "resolve-signed":
            statement = json.loads(Path(args.statement).read_text(encoding="utf-8"))
            result = ledger.resolve_signed(statement, args.signature)
            print(json.dumps({"resolution_id": result.resolution_id}, indent=2))
        elif args.cmd == "forfeit":
            result = ledger.forfeit(args.contract_id, note=args.note)
            print(json.dumps({"disposition_id": result.disposition_id}, indent=2))
        elif args.cmd == "prepare-void":
            print(json.dumps(ledger.prepare_void(
                args.contract_id, reason_code=args.reason_code,
                resolver_key_id=args.resolver_key_id, note=args.note,
            ), indent=2, sort_keys=True))
        elif args.cmd == "void-signed":
            statement = json.loads(Path(args.statement).read_text(encoding="utf-8"))
            result = ledger.void_signed(statement, args.signature)
            print(json.dumps({"disposition_id": result.disposition_id}, indent=2))
        elif args.cmd == "checkpoint":
            print(json.dumps(ledger.checkpoint(), indent=2, sort_keys=True))
        elif args.cmd == "anchor-request":
            print(json.dumps(ledger.anchor_request(args.checkpoint_id), indent=2, sort_keys=True))
        elif args.cmd == "add-anchor":
            statement = json.loads(Path(args.statement).read_text(encoding="utf-8"))
            print(json.dumps(ledger.add_anchor(statement, args.signature), indent=2, sort_keys=True))
        elif args.cmd in {"verify", "report"}:
            trust = {
                "trusted_anchor_keys": _json_file(args.trusted_anchors),
                "trusted_resolver_keys": _json_file(args.trusted_resolvers),
                "external_anchor_receipts": _json_list_file(args.external_anchor_receipts),
            }
            if args.cmd == "verify":
                print(json.dumps(ledger.verify(**trust).to_dict(), indent=2, sort_keys=True))
            else:
                print(build_report(ledger, **trust))
        elif args.cmd == "plot":
            print(reliability_svg(ledger.records(), args.out, minimum_n=args.minimum_n))
    except (ValueError, RetrofitError, LedgerIntegrityError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
