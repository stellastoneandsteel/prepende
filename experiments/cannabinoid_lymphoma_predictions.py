#!/usr/bin/env python3
"""cannabinoid_lymphoma_predictions.py — pre-register (and later resolve) the
cannabinoid / lymphoma evidence study.

  python3 experiments/cannabinoid_lymphoma_predictions.py --lock
  # (step 3) produce experiments/data/cannabinoid_lymphoma_evidence.json from the
  #          fixed literature searches, then:
  python3 experiments/cannabinoid_lymphoma_predictions.py --resolve

Origin: a single n=1 anecdote (a chest mass that resolved during full-spectrum
cannabis-extract use, later characterised as an aggressive lymphoma, gone on
follow-up). n=1 is hypothesis-generating, NOT evidence of causation. The whole
point of locking these contracts before resolving is to let the gate demote even
the hypothesis we most want to be true. This file makes NO treatment claim and is
NOT medical advice; its only output is an honest map of the published evidence.

Each contract resolves against a FIXED, dated literature search (the eval_regime).
The git commit timestamp is the external "locked before resolution" anchor.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from prepende import Ledger, lock_prediction  # noqa: E402

LEDGER = os.path.join(HERE, "predictions.jsonl")
RESULTS = os.path.join(HERE, "data", "cannabinoid_lymphoma_evidence.json")

PREDICTOR = "prepende"

# Fixed search corpus for the literature-resolved contracts. Locked here so the
# resolution cannot be retrofitted to a friendlier search.
LIT_REGIME = ("fixed literature search on lock date over PubMed + Consensus + "
              "ClinicalTrials.gov; queries and inclusion criteria frozen at lock "
              "time; resolved once against that frozen search")

CONTRACTS = [
    {
        "kind": "probability", "claim": {"p": 0.93},
        "question": ("[CL-H1] No interventional human trial demonstrates that "
                     "cannabis/cannabinoids CAUSE remission of lymphoma."),
        "rule": ("y=1 if the frozen ClinicalTrials.gov + PubMed[Clinical Trial] "
                 "search returns 0 interventional trials in which remission is a "
                 "demonstrated cannabinoid effect"),
        "regime": LIT_REGIME,
    },
    {
        "kind": "probability", "claim": {"p": 0.95},
        "question": ("[CL-H2] Preclinical models (in vitro / animal) show "
                     "cannabinoid-induced apoptosis or cell-cycle arrest in "
                     "lymphoma/leukemia cell lines."),
        "rule": "y=1 if >=3 independent preclinical papers report it",
        "regime": LIT_REGIME,
    },
    {
        "kind": "probability", "claim": {"p": 0.90},
        "question": ("[CL-H3] The single human lymphoma trial of whole-plant "
                     "cannabis shows redistribution (not apoptotic killing): no "
                     "caspase-3 activation and a significant malignant-clone "
                     "increase at >=1 week."),
        "rule": ("y=1 if the trial's own results report no caspase-3 activation "
                 "AND a significant clonal increase at follow-up"),
        "regime": "Melen et al. 2019 (Blood) as the fixed primary source",
    },
    {
        "kind": "probability", "claim": {"p": 0.92},
        "question": ("[CL-H4] Spontaneous regression of non-Hodgkin lymphoma is "
                     "documented at >=10% in indolent subtypes (a sufficient "
                     "alternative explanation for an unattributed single remission)."),
        "rule": "y=1 if >=2 independent series report >=10% spontaneous regression in indolent NHL",
        "regime": LIT_REGIME,
    },
    {
        "kind": "probability", "claim": {"p": 0.15},
        "question": ("[CL-H5] A specific CB1 -> DNA-replication-machinery axis "
                     "drives lymphoma cell death (the proposed mechanism)."),
        "rule": ("y=1 if >=2 independent papers establish a direct CB1->DNA-"
                 "replication mechanism causing lymphoma cell death"),
        "regime": LIT_REGIME,
    },
]


def _find_cid(records, question):
    for c, _ in records:
        if c.question == question:
            return c.cid
    return None


def do_lock():
    L = Ledger(LEDGER); recs = L.records(); n = 0
    for spec in CONTRACTS:
        if _find_cid(recs, spec["question"]):
            print("already locked, skipping:", spec["question"][:52], "..."); continue
        c = lock_prediction(PREDICTOR, spec["question"], spec["kind"], spec["claim"],
                            spec["rule"], spec["regime"])
        L.lock(c); n += 1
        print("LOCKED  cid=%s  p=%s  %s..." % (c.cid, spec["claim"], spec["question"][:46]))
    if not n:
        print("nothing new to lock.")
    print("integrity (want []):", L.integrity())


def do_resolve():
    if not os.path.exists(RESULTS):
        sys.exit("produce the frozen-search evidence file first: " + RESULTS)
    res = json.load(open(RESULTS))
    L = Ledger(LEDGER); recs = L.records()
    for spec in CONTRACTS:
        tag = spec["question"].split("]")[0].strip("[")  # e.g. CL-H1
        if tag not in res:
            print("WARN no result for", tag); continue
        entry = res[tag]
        cid = _find_cid(recs, spec["question"])
        if not cid:
            print("WARN no cid for", tag); continue
        L.resolve(cid, {"y": entry["y"]}, spec["regime"], note=entry.get("note", ""))
        print("RESOLVED %s cid=%s y=%s -- %s" % (tag, cid, entry["y"], entry.get("note", "")))
    print("integrity (want []):", L.integrity())


if __name__ == "__main__":
    if "--lock" in sys.argv:
        do_lock()
    elif "--resolve" in sys.argv:
        do_resolve()
    else:
        sys.exit("usage: --lock | --resolve")
