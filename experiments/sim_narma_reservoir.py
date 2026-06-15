#!/usr/bin/env python3
"""
NARMA10 — the standard reservoir-computing benchmark. Does the GAP-coupled architecture help
on a real task, vs a single reservoir of matched total size at matched readout dimensionality?

NARMA10: y(t+1) = 0.3 y(t) + 0.05 y(t) * sum_{i=0..9} y(t-i) + 1.5 u(t-9) u(t) + 0.1,  u ~ U(0,0.5).
The reservoir is driven by u and a linear readout is trained to predict y. Metric: NRMSE (lower better).

Configs (matched readout dim = N):
  GAP    : u -> reservoir A (N) -> thin rank-1 gap -> reservoir B (N); readout from B's N states.
  SINGLE : u -> one reservoir (2N); readout from a random N-subset of its states.

Honest prior mirrors [T2]: the single reservoir sees the input directly, the gap's B sees it only
through a bottleneck, so we expect SINGLE to win. Reports the numbers either way. numpy only.
"""
import os, warnings, json
os.environ.setdefault("VECLIB_MAXIMUM_THREADS", "1")
warnings.filterwarnings("ignore", category=RuntimeWarning)
import numpy as np
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sim_telepathy_hard import make_reservoir

HERE = os.path.dirname(os.path.abspath(__file__))
N = 50
C_GAP = 0.4
SEEDS = list(range(5))


def narma10(T, seed):
    rng = np.random.default_rng(seed)
    u = rng.uniform(0, 0.5, T)
    y = np.zeros(T)
    for t in range(10, T - 1):
        y[t + 1] = (0.3 * y[t] + 0.05 * y[t] * np.sum(y[t - 9:t + 1])
                    + 1.5 * u[t - 9] * u[t] + 0.1)
        if not np.isfinite(y[t + 1]) or abs(y[t + 1]) > 1e3:
            y[t + 1] = 0.0  # guard against rare divergence
    return u, y


def nrmse(y, yp):
    return float(np.sqrt(np.mean((y - yp) ** 2)) / (np.std(y) + 1e-12))


def readout_nrmse(X, y, idx, washout=200, ridge=1e-6):
    Xr, yr = X[washout:][:, idx], y[washout:]
    ntr = int(0.7 * len(Xr))
    W = np.linalg.solve(Xr[:ntr].T @ Xr[:ntr] + ridge * np.eye(len(idx)), Xr[:ntr].T @ yr[:ntr])
    return nrmse(yr[ntr:], Xr[ntr:] @ W)


def run_gap(seed):
    rng = np.random.default_rng(seed)
    u, y = narma10(3000, seed + 3)
    WA, WB = make_reservoir(N, seed + 1), make_reservoir(N, seed + 2)
    Win = rng.standard_normal(N) * 0.1
    GA = rng.standard_normal((1, N)) / np.sqrt(N); VB = rng.standard_normal((N, 1))
    xA = np.zeros(N); xB = np.zeros(N); XB = np.zeros((len(u), N))
    for t in range(len(u)):
        xA = np.tanh(WA @ xA + Win * u[t])
        xB = np.tanh(WB @ xB + C_GAP * (VB @ (GA @ xA)).ravel())
        XB[t] = xB
    return readout_nrmse(XB, y, list(range(N)))


def run_single(seed):
    n2 = 2 * N
    rng = np.random.default_rng(seed)
    u, y = narma10(3000, seed + 3)
    W = make_reservoir(n2, seed + 1); Win = rng.standard_normal(n2) * 0.1
    x = np.zeros(n2); X = np.zeros((len(u), n2))
    for t in range(len(u)):
        x = np.tanh(W @ x + Win * u[t]); X[t] = x
    idx = list(rng.choice(n2, size=N, replace=False))
    return readout_nrmse(X, y, idx)


def main():
    print("NARMA10 reservoir benchmark | gap (2xN) vs single (2N) | matched readout dim N=%d | NRMSE (lower=better)" % N)
    g = [run_gap(s) for s in SEEDS]
    s = [run_single(s) for s in SEEDS]
    gm, sm = float(np.mean(g)), float(np.mean(s))
    print("  GAP    NRMSE: %.4f  (per-seed %s)" % (gm, [round(x, 3) for x in g]))
    print("  SINGLE NRMSE: %.4f  (per-seed %s)" % (sm, [round(x, 3) for x in s]))
    winner = "gap" if gm < sm else "single"
    print("  winner (lower NRMSE): %s" % winner)
    out = {"study": "narma10-gap-vs-single", "N": N, "c_gap": C_GAP, "seeds": SEEDS,
           "gap_nrmse": round(gm, 4), "single_nrmse": round(sm, 4), "winner": winner}
    json.dump(out, open(os.path.join(HERE, "data", "narma10.json"), "w"), indent=2)
    print("wrote data/narma10.json")


if __name__ == "__main__":
    main()
