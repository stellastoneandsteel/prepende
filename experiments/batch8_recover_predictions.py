#!/usr/bin/env python3
"""batch8_recover_predictions.py — pre-register and (later) resolve Batch 8.

--lock writes three falsifiable contracts BEFORE the scored recovery run; --resolve closes
them against experiments/data/reservoir_recover.json under the IDENTICAL eval_regime.

  python3 experiments/batch8_recover_predictions.py --lock      # before the run (commit+push)
  python3 experiments/sim_reservoir_recover.py                   # scored run
  python3 experiments/batch8_recover_predictions.py --resolve    # close contracts
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from prepende import Ledger, lock_prediction  # noqa: E402

LEDGER = os.path.join(HERE, "predictions.jsonl")
RESULTS = os.path.join(HERE, "data", "reservoir_recover.json")

REGIME = ("temporal-XOR with fair measurement-averaging (M independent N(0,sigma) reads in "
          "feature-std units averaged before the ridge readout, identical operator for "
          "reservoir and linear delay-line) at sigma=0.3 on the coupled sub-threshold "
          "parametron reservoir (sim_reservoir_recover.py); Batch-6 regime otherwise: "
          "hyperparameters frozen at clean(sigma=0) VAL, readout retrained per M, noise on "
          "train+test, 5 seeds; margin = reservoir_test_acc - linear_test_acc")

PREDICTOR = "prepende"

RECOVER = {
    "kind": "probability", "claim": {"p": 0.72},
    "question": ("[B8-recover] The noise-fragile temporal-XOR advantage is RECOVERABLE by "
                 "fair measurement-averaging: averaging M=16 independent readout reads at "
                 "sigma=0.3 restores the margin (reservoir_test_acc - linear_test_acc) to at "
                 "least 0.10 -- i.e. the Batch-6 collapse was a readout-measurement problem, "
                 "not a loss of the reservoir's nonlinear-memory computation."),
    "rule": "y=1 if margin_at_M16_sigma0.3 >= 0.10",
}
CONTROL = {
    "kind": "probability", "claim": {"p": 0.88},
    "question": ("[B8-control] Single-shot still collapses: with NO averaging (M=1) at "
                 "sigma=0.3 the margin stays below 0.10, reproducing the Batch-6 result in "
                 "this harness so that any recovery is attributable to averaging, not a "
                 "change of setup."),
    "rule": "y=1 if margin_at_M1_sigma0.3 < 0.10",
}
DOSE = {
    "kind": "probability", "claim": {"p": 0.55},
    "question": ("[B8-dose] Recovery has a dose-response: token averaging is not enough -- "
                 "at M=4 reads (sigma=0.3) the margin is still below 0.10, so meaningful "
                 "averaging (M~16, effective sigma~0.075) is required, not a couple of reads "
                 "(M=4, effective sigma~0.15)."),
    "rule": "y=1 if margin_at_M4_sigma0.3 < 0.10",
}


def _find_cid(records, question):
    for c, _ in records:
        if c.question == question:
            return c.cid
    return None


def do_lock():
    L = Ledger(LEDGER); recs = L.records(); n = 0
    for spec in (RECOVER, CONTROL, DOSE):
        if _find_cid(recs, spec["question"]):
            print("already locked, skipping:", spec["question"][:48], "..."); continue
        c = lock_prediction(PREDICTOR, spec["question"], spec["kind"], spec["claim"],
                            spec["rule"], REGIME)
        L.lock(c); n += 1
        print("LOCKED  cid=%s  p=%s  %s..." % (c.cid, spec["claim"], spec["question"][:44]))
    if not n:
        print("nothing new to lock.")
    print("integrity (want []):", L.integrity())


def do_resolve():
    if not os.path.exists(RESULTS):
        sys.exit("run sim_reservoir_recover.py first: " + RESULTS)
    res = json.load(open(RESULTS))
    L = Ledger(LEDGER); recs = L.records()
    r = res["resolved"]
    m1, m4, m16 = res["margin_M1"], res["margin_M4"], res["margin_M16"]
    mapping = [
        (RECOVER["question"], {"y": r["recover_y"]},
         "margin(M=16, sigma0.3) = %.3f -- %s" % (m16, "HIT" if r["recover_y"] else "MISS")),
        (CONTROL["question"], {"y": r["control_y"]},
         "margin(M=1, sigma0.3) = %.3f -- %s" % (m1, "HIT" if r["control_y"] else "MISS")),
        (DOSE["question"], {"y": r["dose_y"]},
         "margin(M=4, sigma0.3) = %.3f -- %s" % (m4, "HIT" if r["dose_y"] else "MISS")),
    ]
    for q, outcome, note in mapping:
        cid = _find_cid(recs, q)
        if not cid:
            print("WARN no cid for", q[:40]); continue
        L.resolve(cid, outcome, REGIME, note=note)
        print("RESOLVED cid=%s  %s -> %s" % (cid, outcome, note))
    print("integrity (want []):", L.integrity())


if __name__ == "__main__":
    if "--lock" in sys.argv:
        do_lock()
    elif "--resolve" in sys.argv:
        do_resolve()
    else:
        sys.exit("usage: --lock | --resolve")
