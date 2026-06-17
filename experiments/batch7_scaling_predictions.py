#!/usr/bin/env python3
"""batch7_scaling_predictions.py — pre-register and (later) resolve Batch 7.

  python3 experiments/batch7_scaling_predictions.py --lock
  python3 experiments/sim_reservoir_scaling.py
  python3 experiments/batch7_scaling_predictions.py --resolve
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from prepende import Ledger, lock_prediction  # noqa: E402

LEDGER = os.path.join(HERE, "predictions.jsonl")
RESULTS = os.path.join(HERE, "data", "reservoir_scaling.json")

REGIME = ("temporal-XOR with reservoir size N swept {50,100,200,400} at fixed dynamics "
          "(pfrac=0.5,c=0.3,isc=2.0) on the coupled sub-threshold parametron reservoir "
          "(sim_reservoir_scaling.py); clean and sigma=0.3 readout-feature noise, noise "
          "identical to reservoir and linear delay-line, readout retrained per condition, "
          "5 seeds; margin = reservoir_test_acc - linear_test_acc")

PREDICTOR = "prepende"

PRIMARY = {
    "kind": "probability", "claim": {"p": 0.50},
    "question": ("[B7] Scale rescues noise-robustness: a larger parametron reservoir "
                 "(N=200) restores the temporal-XOR advantage under noise that collapsed "
                 "at N=50 -- at sigma=0.3 readout noise the N=200 margin "
                 "(reservoir_test_acc - linear_test_acc) is at least 0.10."),
    "rule": "y=1 if margin_at_N200_sigma0.3 >= 0.10",
}
CONTROL = {
    "kind": "probability", "claim": {"p": 0.70},
    "question": ("[B7-control] Scale improves clean capacity: the N=400 reservoir's clean "
                 "(noise-free) temporal-XOR test accuracy exceeds the N=50 reservoir's by "
                 "at least 0.05."),
    "rule": "y=1 if clean_acc(N=400) - clean_acc(N=50) >= 0.05",
}


def _find_cid(records, question):
    for c, _ in records:
        if c.question == question:
            return c.cid
    return None


def do_lock():
    L = Ledger(LEDGER); recs = L.records(); n = 0
    for spec in (PRIMARY, CONTROL):
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
        sys.exit("run sim_reservoir_scaling.py first: " + RESULTS)
    res = json.load(open(RESULTS))
    L = Ledger(LEDGER); recs = L.records()
    r = res["resolved"]
    m200 = res["margin_at_N200_sigma0.3"]
    gain = res["clean_acc_gain_N50_to_N400"]
    mapping = [
        (PRIMARY["question"], {"y": r["scale_restores_noise_robustness_y"]},
         "margin(N=200, sigma0.3) = %.3f -- %s" % (m200,
            "HIT" if r["scale_restores_noise_robustness_y"] else "MISS")),
        (CONTROL["question"], {"y": r["scale_improves_clean_capacity_y"]},
         "clean acc gain N50->N400 = %+.3f -- %s" % (gain,
            "HIT" if r["scale_improves_clean_capacity_y"] else "MISS")),
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
