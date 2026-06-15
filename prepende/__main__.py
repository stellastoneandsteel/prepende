"""CLI:  python3 -m prepende <lock|resolve|report|plot> [...]"""
from __future__ import annotations

import argparse
import json
import sys

from .contract import lock_prediction
from .ledger import Ledger, RetrofitError
from .plot import reliability_svg
from .report import build_report


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="prepende",
                                 description="pre-registered, hash-locked AI prediction calibration")
    ap.add_argument("--ledger", default="ledger.jsonl", help="path to the JSONL ledger (commit it to git)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    pl = sub.add_parser("lock", help="lock a prediction BEFORE the outcome is known")
    pl.add_argument("--predictor", required=True)
    pl.add_argument("--question", required=True)
    pl.add_argument("--kind", default="probability", choices=["probability", "numeric", "categorical"])
    pl.add_argument("--claim", required=True, help='JSON, e.g. {"p":0.7}')
    pl.add_argument("--rule", required=True, help="how it resolves")
    pl.add_argument("--regime", required=True, help="the fixed eval conditions (locked)")

    pr = sub.add_parser("resolve", help="resolve a locked prediction (regime must match)")
    pr.add_argument("--cid", required=True)
    pr.add_argument("--outcome", required=True, help='JSON, e.g. {"y":1}')
    pr.add_argument("--regime", required=True)
    pr.add_argument("--note", default="")

    sub.add_parser("report", help="print the calibration report")
    pp = sub.add_parser("plot", help="write a reliability-diagram SVG")
    pp.add_argument("--out", default="calibration.svg")

    a = ap.parse_args(argv)
    L = Ledger(a.ledger)

    if a.cmd == "lock":
        c = lock_prediction(a.predictor, a.question, a.kind, json.loads(a.claim), a.rule, a.regime)
        L.lock(c)
        print("locked %s   hash=%s" % (c.cid, c.lock_hash))
    elif a.cmd == "resolve":
        try:
            r = L.resolve(a.cid, json.loads(a.outcome), a.regime, note=a.note)
            print("resolved %s" % r.cid)
        except RetrofitError as e:
            print("REFUSED: %s" % str(e).splitlines()[0])
            sys.exit(2)
    elif a.cmd == "report":
        print(build_report(L))
    elif a.cmd == "plot":
        resolved = [(c, r) for c, r in L.records() if r is not None]
        print("wrote %s" % reliability_svg(resolved, a.out))


if __name__ == "__main__":
    main()
