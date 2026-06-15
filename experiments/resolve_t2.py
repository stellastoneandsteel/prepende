#!/usr/bin/env python3
"""Resolve the pre-registered [T2] prediction from sim_gap_reservoir.py's result.

[T2] was locked BEFORE the run (predictions.jsonl, git history proves order). The
gap-coupled reservoir did NOT beat a single reservoir at matched readout, so [T2]
resolves y=0 -- a clean, pre-registered miss. The eval_regime passed here must match
the locked regime exactly, or the ledger refuses the resolution (anti-retrofit).
"""
import os, sys, json

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from prepende import Ledger  # noqa: E402

LEDGER = os.path.join(HERE, "predictions.jsonl")
DATA = os.path.join(HERE, "data", "gap-reservoir-t2.json")


def main():
    res = json.load(open(DATA, encoding="utf-8"))
    y = int(res["t2_outcome_y"])
    L = Ledger(LEDGER)
    target = None
    for c, r in L.records():
        if c.question.startswith("[T2]"):
            target = (c, r)
            break
    if target is None:
        raise SystemExit("[T2] contract not found in ledger")
    c, r = target
    if r is not None:
        print("[T2] already resolved:", r.outcome)
        return
    note = ("gap mean R^2=%.3f vs single(matched)=%.3f; hypothesis not supported, "
            "point estimate contrary" % (res["gap_mean_r2"], res["single_matched_mean_r2"]))
    L.resolve(c.cid, {"y": y}, c.eval_regime, note=note)
    print("[T2] resolved y=%d (%s)" % (y, note))
    print("cid=%s" % c.cid)


if __name__ == "__main__":
    main()
