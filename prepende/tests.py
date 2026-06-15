"""Test suite:  python3 -m prepende.tests"""
from __future__ import annotations

import os
import tempfile

from .contract import lock_prediction
from .ledger import Ledger, RetrofitError
from .metrics import reliability, wilson
from .scoring import brier


def t(name: str, cond: bool) -> None:
    print(("PASS" if cond else "FAIL") + "  " + name)
    assert cond, name


def run() -> None:
    # hashing / tamper
    c = lock_prediction("p", "q", "probability", {"p": 0.7}, "rule", "regime", created_at=1.0)
    t("locks and verifies", c.verify())
    h = c.lock_hash
    c.claim["p"] = 0.1
    t("tamper breaks the hash", (not c.verify()) and c.compute_hash() != h)

    # scoring
    t("brier perfect = 0", brier(1, 1) == 0 and brier(0, 0) == 0)
    t("brier worst = 1", brier(1, 0) == 1)
    t("wilson in [0,1] and ordered", 0 <= wilson(7, 10)[0] <= wilson(7, 10)[1] <= 1)

    # ledger + anti-retrofit
    path = tempfile.mktemp(suffix=".jsonl")
    L = Ledger(path)
    c2 = lock_prediction("p", "q2", "probability", {"p": 0.6}, "rule", "regimeA", created_at=2.0)
    L.lock(c2)
    refused = False
    try:
        L.resolve(c2.cid, {"y": 1}, "regimeB")  # changed regime
    except RetrofitError:
        refused = True
    t("goalpost-move (regime change) refused", refused)
    L.resolve(c2.cid, {"y": 1}, "regimeA")
    dbl = False
    try:
        L.resolve(c2.cid, {"y": 0}, "regimeA")
    except RetrofitError:
        dbl = True
    t("double-resolve refused", dbl)
    t("integrity clean", L.integrity() == [])

    # calibration: a well-calibrated set should have small ECE
    path2 = tempfile.mktemp(suffix=".jsonl")
    L2 = Ledger(path2)
    for i in range(100):
        cc = lock_prediction("p", "q%d" % i, "probability", {"p": 0.7}, "r", "reg", created_at=float(i))
        L2.lock(cc)
        L2.resolve(cc.cid, {"y": 1 if i % 10 < 7 else 0}, "reg")
    rel = reliability(L2.records())
    t("ECE small for a calibrated set", rel["ece"] < 0.06)
    t("base rate recovered ~0.7", abs(rel["base_rate"] - 0.7) < 1e-9)

    os.remove(path)
    os.remove(path2)
    print("\nALL TESTS PASSED")


if __name__ == "__main__":
    run()
