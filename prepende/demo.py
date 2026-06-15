"""Demo: seed a ledger, print the calibration report, write the SVG, prove the teeth.

Run from the repo root:  python3 -m prepende.demo
Uses generic predictors only (no private architecture is referenced).
"""
from __future__ import annotations

import json
import os

from prepende.contract import lock_prediction
from prepende.ledger import Ledger, RetrofitError
from prepende.plot import reliability_svg
from prepende.report import build_report

HERE = os.path.dirname(os.path.abspath(__file__))
LEDGER = os.path.join(HERE, "demo_ledger.jsonl")
SVG = os.path.join(HERE, "calibration.svg")


def seed() -> Ledger:
    if os.path.exists(LEDGER):
        os.remove(LEDGER)
    L = Ledger(LEDGER)
    t = 1_700_000_000.0
    # synthetic probabilistic forecasts (deliberately a touch overconfident, to show a real curve)
    probs = [(0.9, 1), (0.85, 1), (0.8, 1), (0.95, 1), (0.7, 1), (0.6, 0), (0.55, 1), (0.5, 0),
             (0.4, 0), (0.3, 0), (0.2, 0), (0.75, 1), (0.65, 1), (0.9, 0), (0.85, 1), (0.7, 0),
             (0.6, 1), (0.45, 0), (0.8, 1), (0.35, 0)]
    for i, (p, y) in enumerate(probs):
        c = lock_prediction("agent-A", "forecast #%d (held-out question)" % i, "probability",
                            {"p": p}, "resolves y=1 if the event occurs", "benchmark-v1", created_at=t + i)
        L.lock(c)
        L.resolve(c.cid, {"y": y}, "benchmark-v1")
    # a couple of numeric forecasts with stated CIs
    c2 = lock_prediction("agent-A", "p95 latency of the build (ms)", "numeric",
                         {"value": 120.0, "unit": "ms", "lo": 90.0, "hi": 150.0},
                         "observed p95", "benchmark-v1", created_at=t + 100)
    L.lock(c2); L.resolve(c2.cid, {"value": 132.0}, "benchmark-v1", note="within CI")
    c3 = lock_prediction("agent-A", "task accuracy on eval set", "numeric",
                         {"value": 0.90, "unit": "acc", "lo": 0.85, "hi": 0.95},
                         "observed accuracy", "benchmark-v1", created_at=t + 101)
    L.lock(c3); L.resolve(c3.cid, {"value": 0.78}, "benchmark-v1", note="overconfident, out of CI")
    # a live PENDING prediction (left unresolved on purpose)
    c4 = lock_prediction("agent-A", "Will the next release pass CI on the first attempt?",
                         "probability", {"p": 0.8}, "resolves y=1 if first CI run is green",
                         "ci-pipeline-v3", created_at=t + 102)
    L.lock(c4)
    return L


def main() -> None:
    L = seed()
    print(build_report(L))
    print("\nwrote reliability diagram: %s" % reliability_svg(
        [(c, r) for c, r in L.records() if r is not None], SVG, nbins=5))

    print("\n" + "-" * 64)
    print("ANTI-RETROFIT 1 — moving the goalposts is refused:")
    pending = [c for c, r in L.records() if r is None][0]
    try:
        L.resolve(pending.cid, {"y": 1}, "ci-pipeline-v4 (changed!)")
        print("  BUG: retrofit was allowed")
    except RetrofitError as e:
        print("  refused as designed: " + str(e).splitlines()[0])

    print("\nANTI-RETROFIT 2 — tampering with a locked prediction is detected:")
    with open(LEDGER, encoding="utf-8") as f:
        lines = f.readlines()
    for i, ln in enumerate(lines):
        row = json.loads(ln)
        if row.get("type") == "contract" and row.get("claim", {}).get("p") == 0.9:
            row["claim"]["p"] = 0.1  # secretly change the prediction after the fact
            lines[i] = json.dumps(row, sort_keys=True) + "\n"
            break
    with open(LEDGER, "w", encoding="utf-8") as f:
        f.writelines(lines)
    bad = L.integrity()
    print("  integrity check after tamper: %s" % ("CAUGHT " + str(bad) if bad else "BUG: missed"))


if __name__ == "__main__":
    main()
