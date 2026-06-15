#!/usr/bin/env python3
"""
[T2] test: does a GAP-COUPLED two-reservoir system recover a noisy signal better than a
SINGLE reservoir of matched total size, at matched readout dimensionality?

Pre-registered in the ledger (predictions.jsonl) BEFORE this was run:
  gap = 2 x N  vs  single = 2N ;  readout dim = N ;  noise in {0.1,0.3,0.5,0.7} ; 5 seeds.
  [T2] y=1 iff gap mean-R^2 (across noise) > single mean-R^2 at matched readout dim N.

Honest prior was p=0.45 (slightly AGAINST: the single reservoir sees the signal directly,
the gap's B reservoir only sees it through a thin bottleneck). This script just reports the
numbers; resolve_t2.py reads them and resolves the ledger entry either way.

Reuses the reservoir primitives from sim_telepathy_hard.py. numpy required; cosmetic
Accelerate matmul warnings are false positives (BLAS self-check in that file).
"""
import os, sys, json, warnings
os.environ.setdefault("VECLIB_MAXIMUM_THREADS", "1")
warnings.filterwarnings("ignore", category=RuntimeWarning)
import numpy as np  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from sim_telepathy_hard import make_reservoir, private_signal  # noqa: E402

N = 60            # per-reservoir size; gap total = 2N = 120; single = 120
C_GAP = 0.4       # fixed gap coupling strength (feedforward A->B)
NOISES = [0.1, 0.3, 0.5, 0.7]
SEEDS = list(range(5))


def ridge_r2(X, y, washout=300, ridge=1e-4):
    X = X[washout:]; y = y[washout:]
    ntr = int(0.7 * len(X))
    W = np.linalg.solve(X[:ntr].T @ X[:ntr] + ridge * np.eye(X.shape[1]), X[:ntr].T @ y[:ntr])
    pred = X[ntr:] @ W
    return float(1.0 - np.sum((y[ntr:] - pred) ** 2) / (np.sum((y[ntr:] - y[ntr:].mean()) ** 2) + 1e-12))


def run_gap(seed, noise):
    rng = np.random.default_rng(seed)
    WA = make_reservoir(N, seed + 1); WB = make_reservoir(N, seed + 2)
    Win = rng.standard_normal(N) * 0.5
    GA = rng.standard_normal((1, N)) / np.sqrt(N); VB = rng.standard_normal((N, 1))
    u = private_signal(2000, seed + 3); T = len(u)
    nrng = np.random.default_rng(seed + 7)
    xA = np.zeros(N); xB = np.zeros(N); XB = np.zeros((T, N))
    for t in range(T):
        xA = np.tanh(WA @ xA + Win * u[t] + noise * nrng.standard_normal(N))
        xB = np.tanh(WB @ xB + C_GAP * (VB @ (GA @ xA)).ravel() + noise * nrng.standard_normal(N))
        XB[t] = xB
    return ridge_r2(XB, u)  # readout dim = N (B only)


def run_single(seed, noise, readout_dim=N):
    n2 = 2 * N
    rng = np.random.default_rng(seed)
    W = make_reservoir(n2, seed + 1); Win = rng.standard_normal(n2) * 0.5
    u = private_signal(2000, seed + 3); T = len(u)
    nrng = np.random.default_rng(seed + 7)
    x = np.zeros(n2); X = np.zeros((T, n2))
    for t in range(T):
        x = np.tanh(W @ x + Win * u[t] + noise * nrng.standard_normal(n2)); X[t] = x
    idx = rng.choice(n2, size=readout_dim, replace=False)
    return ridge_r2(X[:, idx], u)  # matched readout dim = N


def main():
    print("[T2] gap-coupled 2xN vs single 2N reservoir, matched readout dim N=%d, c_gap=%.2f" % (N, C_GAP))
    print("%8s%14s%14s%16s" % ("noise", "GAP R^2", "SINGLE R^2", "single(full 2N)"))
    rows = []
    for noise in NOISES:
        g = [run_gap(s, noise) for s in SEEDS]
        sm = [run_single(s, noise, N) for s in SEEDS]
        sf = [run_single(s, noise, 2 * N) for s in SEEDS]
        gm, smm, sfm = float(np.mean(g)), float(np.mean(sm)), float(np.mean(sf))
        rows.append({"noise": noise, "gap_r2": round(gm, 4),
                     "single_matched_r2": round(smm, 4), "single_full_r2": round(sfm, 4)})
        print("%8.2f%13.3f %13.3f %15.3f" % (noise, gm, smm, sfm))
    gap_mean = float(np.mean([r["gap_r2"] for r in rows]))
    single_mean = float(np.mean([r["single_matched_r2"] for r in rows]))
    verdict = gap_mean > single_mean
    print("-" * 52)
    print("mean across noise:  GAP=%.3f   SINGLE(matched)=%.3f" % (gap_mean, single_mean))
    print("[T2] gap > single at matched readout? -> %s" % ("YES (y=1)" if verdict else "NO (y=0)"))
    out = {"study": "T2-gap-vs-single-reservoir", "N": N, "c_gap": C_GAP,
           "noises": NOISES, "seeds": SEEDS, "rows": rows,
           "gap_mean_r2": round(gap_mean, 4), "single_matched_mean_r2": round(single_mean, 4),
           "t2_outcome_y": 1 if verdict else 0}
    with open(os.path.join(HERE, "data", "gap-reservoir-t2.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    print("wrote data/gap-reservoir-t2.json")


if __name__ == "__main__":
    main()
