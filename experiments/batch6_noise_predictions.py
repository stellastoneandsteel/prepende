#!/usr/bin/env python3
"""batch6_noise_predictions.py — pre-register and (later) resolve Batch 6.

--lock writes two falsifiable contracts BEFORE the scored noise sweep is run; --resolve
closes them against experiments/data/reservoir_noise.json under the IDENTICAL eval_regime.

  python3 experiments/batch6_noise_predictions.py --lock
  python3 experiments/sim_reservoir_noise.py
  python3 experiments/batch6_noise_predictions.py --resolve
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from prepende import Ledger, lock_prediction  # noqa: E402

LEDGER = os.path.join(HERE, "predictions.jsonl")
RESULTS = os.path.join(HERE, "data", "reservoir_noise.json")

REGIME = ("temporal-XOR under Gaussian readout-feature noise (feature-std units) on the "
          "coupled sub-threshold parametron reservoir (sim_reservoir_noise.py); "
          "hyperparameters frozen at clean(sigma=0) VAL, readout retrained per noise "
          "level, noise on train+test applied identically to reservoir and linear "
          "delay-line; 5 seeds; margin = reservoir_test_acc - linear_test_acc")

PREDICTOR = "prepende"

PRIMARY = {
    "kind": "probability", "claim": {"p": 0.70},
    "question": ("[B6] The parametron reservoir's temporal-XOR advantage over a trained "
                 "linear delay-line SURVIVES moderate readout noise: at Gaussian feature "
                 "noise sigma=0.3 (feature-std units) the margin (reservoir_test_acc - "
                 "linear_test_acc) is still at least 0.10."),
    "rule": "y=1 if margin_at_sigma_0.3 >= 0.10",
}
BOUNDARY = {
    "kind": "probability", "claim": {"p": 0.60},
    "question": ("[B6-boundary] The advantage has a noise ceiling: at heavy readout noise "
                 "sigma=1.0 the margin (reservoir_test_acc - linear_test_acc) falls below "
                 "0.10, i.e. the parametron reservoir's nonlinear-memory edge is not "
                 "noise-proof and degrades to near-parity under heavy noise."),
    "rule": "y=1 if margin_at_sigma_1.0 < 0.10",
}


def _find_cid(records, question):
    for c, _ in records:
        if c.question == question:
            return c.cid
    return None


def do_lock():
    L = Ledger(LEDGER); recs = L.records(); n = 0
    for spec in (PRIMARY, BOUNDARY):
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
        sys.exit("run sim_reservoir_noise.py first: " + RESULTS)
    res = json.load(open(RESULTS))
    L = Ledger(LEDGER); recs = L.records()
    r = res["resolved"]
    m03, m10 = res["margin_at_0.3"], res["margin_at_1.0"]
    mapping = [
        (PRIMARY["question"], {"y": r["advantage_survives_moderate_noise_y"]},
         "margin@sigma0.3 = %.3f -- %s" % (m03,
            "HIT" if r["advantage_survives_moderate_noise_y"] else "MISS")),
        (BOUNDARY["question"], {"y": r["advantage_erodes_under_heavy_noise_y"]},
         "margin@sigma1.0 = %.3f -- %s" % (m10,
            "HIT" if r["advantage_erodes_under_heavy_noise_y"] else "MISS")),
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
