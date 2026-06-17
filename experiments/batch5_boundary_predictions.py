#!/usr/bin/env python3
"""batch5_boundary_predictions.py — pre-register and (later) resolve Batch 5.

Blueprint discipline (no direction): --lock writes two falsifiable contracts into the
PUBLIC ledger BEFORE the scored sim is run; the git commit of that lock is the external,
tamper-evident timestamp. --resolve reads the sim's results JSON and closes each contract
under the IDENTICAL eval_regime (a changed regime is refused by prepende.RetrofitError).

The eval_regime / question / rule strings live here as constants so the lock and the
resolve use byte-identical text.

  python3 experiments/batch5_boundary_predictions.py --lock      # before the run
  python3 experiments/sim_reservoir_boundary.py                  # the scored run
  python3 experiments/batch5_boundary_predictions.py --resolve   # after the run
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from prepende import Ledger, lock_prediction  # noqa: E402

LEDGER = os.path.join(HERE, "predictions.jsonl")
RESULTS = os.path.join(HERE, "data", "reservoir_boundary.json")

REGIME = ("temporal-XOR(D1=2,D2=4) on coupled sub-threshold parametron reservoir "
          "envelope model (sim_reservoir_boundary.py); ridge readout to +/-1; "
          "hyperparameters selected on VAL accuracy and reported on held-out TEST; "
          "5 seeds; linear delay-line and tanh ESN baselines")

PREDICTOR = "prepende"

PRIMARY = {
    "kind": "probability", "claim": {"p": 0.70},
    "question": ("[B5] On a temporal-XOR task (target y(t)=b(t-2) XOR b(t-4), "
                 "b(t)=1[u(t)>0.25], u~U(0,0.5)), the coupled sub-threshold parametron "
                 "reservoir's held-out TEST classification accuracy exceeds a trained "
                 "linear delay-line readout's by at least 15 percentage points -- i.e. "
                 "the parametron fabric is decisively useful on a nonlinear-memory task, "
                 "the regime where NARMA10 (Batch 4) hid any advantage."),
    "rule": "y=1 if reservoir_test_acc - linear_test_acc >= 0.15",
}
CONTROL = {
    "kind": "probability", "claim": {"p": 0.85},
    "question": ("[B5-control] On the same temporal-XOR task, a trained linear delay-line "
                 "readout stays below 60% held-out TEST accuracy (near the 50% chance "
                 "level), confirming the task is genuinely nonlinear and not solvable by "
                 "a linear filter."),
    "rule": "y=1 if linear_test_acc < 0.60",
}


def _find_cid(records, question):
    for c, _res in records:
        if c.question == question:
            return c.cid
    return None


def do_lock():
    L = Ledger(LEDGER)
    recs = L.records()
    locked = []
    for spec in (PRIMARY, CONTROL):
        if _find_cid(recs, spec["question"]):
            print("already locked, skipping:", spec["question"][:48], "...")
            continue
        c = lock_prediction(PREDICTOR, spec["question"], spec["kind"], spec["claim"],
                            spec["rule"], REGIME)
        L.lock(c)
        locked.append((c.cid, spec["claim"]))
        print("LOCKED  cid=%s  p=%s  %s..." % (c.cid, spec["claim"], spec["question"][:44]))
    if not locked:
        print("nothing new to lock.")
    print("integrity (tampered cids, want []):", L.integrity())


def do_resolve():
    if not os.path.exists(RESULTS):
        sys.exit("results not found -- run sim_reservoir_boundary.py first: " + RESULTS)
    res = json.load(open(RESULTS))
    L = Ledger(LEDGER)
    recs = L.records()
    rdict = res["resolved"]
    margin = res["reservoir_minus_linear_test_acc"]
    lin_te = res["linear_delayline"]["test_acc"]
    rsv_te = res["parametron_reservoir"]["test_acc"]
    esn_te = res["esn_reference"]["test_acc"]
    mapping = [
        (PRIMARY["question"], {"y": rdict["reservoir_beats_linear_by_15pts_y"]},
         "reservoir test acc %.3f vs linear %.3f (margin %+.3f; ESN %.3f) -- %s"
         % (rsv_te, lin_te, margin, esn_te,
            "HIT" if rdict["reservoir_beats_linear_by_15pts_y"] else "MISS")),
        (CONTROL["question"], {"y": rdict["linear_near_chance_below_0.60_y"]},
         "linear test acc %.3f -- %s"
         % (lin_te, "HIT" if rdict["linear_near_chance_below_0.60_y"] else "MISS")),
    ]
    for question, outcome, note in mapping:
        cid = _find_cid(recs, question)
        if not cid:
            print("WARN no locked cid for:", question[:48]); continue
        L.resolve(cid, outcome, REGIME, note=note)
        print("RESOLVED cid=%s  %s -> %s" % (cid, outcome, note))
    print("integrity (tampered cids, want []):", L.integrity())


if __name__ == "__main__":
    if "--lock" in sys.argv:
        do_lock()
    elif "--resolve" in sys.argv:
        do_resolve()
    else:
        sys.exit("usage: --lock | --resolve")
