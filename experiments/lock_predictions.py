#!/usr/bin/env python3
"""
Seed the PUBLIC prediction ledger (experiments/predictions.jsonl) via the prepende API
so every entry carries a real SHA-256 lock hash. The git commit timestamp is the
external, tamper-evident "locked before outcome" anchor.

Two honestly-separated groups:
  * predictor "prepende:dev-selftest" -- development self-tests. These were recorded
    RETROSPECTIVELY (lock and resolve in the same dev cycle); they are NOT pre-registered
    ahead of outcome. Kept because one is a logged MISS and they exercise the tool.
  * predictor "prepende" -- FORWARD pre-registered predictions, locked now, resolved
    later in the open. This is the audited track record (gate c).

Re-runnable: truncates the ledger and rebuilds it deterministically (except timestamps).
The relational-constraint prediction is stated generically about an AI's action-gates.
"""
import os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from prepende import Ledger, lock_prediction, summary, resolvability  # noqa: E402

LEDGER = os.path.join(HERE, "predictions.jsonl")
NOW = time.time()

# (predictor, kind, claim, question, resolution_rule, eval_regime, resolution_or_None)
DEV = "prepende:dev-selftest"
FWD = "prepende"

DEV_SELFTESTS = [
    (DEV, "probability", {"p": 0.9},
     "Build A reaches the optimum in >=90% of restarts on a fresh 5-node Max-Cut",
     "y=1 if hit-rate>=90%", "OIM 50 restarts, anneal-v1",
     {"y": 1}, "observed 100%"),
    (DEV, "probability", {"p": 0.85},
     "OIM stays >=99% of best-known cut up to n=120 (random p=0.5)",
     "y=1 if min ratio>=0.99", "OIM 50 restarts vs best-of-both",
     {"y": 1}, "0.997-1.0 observed"),
    (DEV, "probability", {"p": 0.6},
     "OIM beats simulated annealing on best cut at n>=80",
     "y=1 if OIM best > SA best", "random p=0.5, matched effort",
     {"y": 0}, "MISS: SA edged it (kept honest)"),
    (DEV, "numeric", {"value": 0.997, "lo": 0.99, "hi": 1.0, "unit": "ratio"},
     "OIM/best cut ratio at n=120", "observed ratio", "OIM 50 restarts vs best-of-both",
     {"value": 0.997}, "hit"),
]

FORWARD = [
    (FWD, "probability", {"p": 0.80},
     "By 2026-12-31, no replicated result shows a coupled-oscillator / Ising machine BEATING (not tying) the best classical Max-Cut solver at n>=80",
     "y=1 if no independently-replicated beat exists by 2026-12-31",
     "literature review at 2026-12-31; independent replication required"),
    (FWD, "probability", {"p": 0.65},
     "By 2026-12-31, the 'resolvability axis' (checkability orthogonal to calibration) has not appeared in any independent published work",
     "y=1 if no independent publication of the construct by 2026-12-31",
     "literature review at 2026-12-31"),
    (FWD, "probability", {"p": 0.55},
     "An AI under a visible monitoring signal at its action-gates shows MORE hedging/refusal AND lower output quality than identical gates with no signal",
     "y=1 if monitored condition has higher hedge-rate AND lower quality at p<0.05",
     "matched gates, N>=200 sessions, preregistered metrics, blind rating"),
    (FWD, "probability", {"p": 0.60},
     "Gap-framing (prompting from the tension between two poles) yields higher human-rated depth/originality than single-pole prompting",
     "y=1 if gap-framed mean depth > single-pole at p<0.05, blind-rated",
     "blind human rating, N>=100 matched prompt pairs, preregistered depth rubric"),
    (FWD, "probability", {"p": 0.50},
     "Instruction-free recombination over a fixed corpus surfaces MORE novel-and-valid junctions than directed brainstorming by the same model over the same corpus",
     "y=1 if recombination novel-valid count > directed count, blind-judged",
     "same model + corpus, blind novelty+validity judging, preregistered, N>=3 rounds each"),
    (FWD, "probability", {"p": 0.30},
     "[T1] A physical Build A (coupled-oscillator bench) shows a measured energy-per-solution win vs simulated-annealing-on-CPU at matched cut quality",
     "y=1 if measured joules/solution lower at matched cut quality",
     "bench, real silicon, matched cut quality, joules/solution + time-to-solution measured"),
    (FWD, "probability", {"p": 0.45},
     "[T2] A gap-coupled two-reservoir system achieves higher noise-robust signal recovery than a single reservoir of matched total size at matched readout dimensionality",
     "y=1 if gap-config mean R^2 > single-reservoir mean R^2 at matched readout dim, across the tested noise range",
     "sim_gap_reservoir.py; gap=2xN vs single=2N; readout dim=N; noise in {0.1,0.3,0.5,0.7}; 5 seeds; locked before running"),
]


def main():
    open(LEDGER, "w").close()  # truncate -> deterministic rebuild
    L = Ledger(LEDGER)
    n_dev = n_fwd = 0
    for predictor, kind, claim, q, rule, regime, outcome, note in DEV_SELFTESTS:
        c = lock_prediction(predictor, q, kind, claim, rule, regime, created_at=NOW)
        L.lock(c)
        L.resolve(c.cid, outcome, regime, note=note, resolved_at=NOW)
        n_dev += 1
    for predictor, kind, claim, q, rule, regime in FORWARD:
        c = lock_prediction(predictor, q, kind, claim, rule, regime, created_at=NOW)
        L.lock(c)
        n_fwd += 1
    recs = L.records()
    s = summary(recs)
    weak = sum(1 for cc, _ in recs if resolvability(cc)["score"] < 0.6)
    print("locked %d dev self-tests (resolved) + %d forward predictions (pending)" % (n_dev, n_fwd))
    print("calibration over resolved prob predictions: n=%d  Brier=%.3f  skill=%+.3f"
          % (s.get("n_prob", 0), s.get("brier", float("nan")), s.get("skill", float("nan"))))
    print("resolvability: %d/%d flagged WEAK (<0.60)" % (weak, len(recs)))
    print("integrity (tampered cids, want []):", L.integrity())
    print("wrote", LEDGER)


if __name__ == "__main__":
    main()
