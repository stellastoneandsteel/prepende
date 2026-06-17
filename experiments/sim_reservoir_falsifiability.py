#!/usr/bin/env python3
"""reservoir_falsifiability.py — does a coupled sub-threshold parametron network
actually work as a physical reservoir? The falsifiability test the SHIL-reservoir
bridge hypothesis was waiting on.

Each cell is a degenerate parametric oscillator described by its two carrier
quadratures (x, y). Below threshold the pump partially cancels damping (tunable
memory; the model reproduces the ngspice critical-slowing trend tau ~ 1/(lam-p)).
A cubic Stuart-Landau term provides nonlinearity; J couples cells (the "gap"):

    dx_i/dt = (-lam + p) x_i - g r2_i x_i + c (J x)_i + b_i u(t)
    dy_i/dt = (-lam - p) y_i - g r2_i y_i + c (J y)_i
    r2_i = x_i^2 + y_i^2 ,   p = pfrac * p_th ,   p_th = lam

Battery (all honest, pre-registered cid 69bdcdca / fb8e1e97 / 7f5ca541):
  (1) echo-state property: two ICs, same input -> state convergence?
  (2) memory capacity: sum_k corr(readout, u(t-k))^2
  (3) NARMA10 test NRMSE vs baselines:
        - uncoupled bank (c=0)        -> does coupling help?
        - trained linear delay line   -> does the reservoir beat a linear filter?
        - a standard tanh ESN         -> reference reservoir
Sweeps pfrac (memory depth) x c (coupling) x seeds. Pure numpy.
"""
import json
from pathlib import Path
import numpy as np

HERE = Path(__file__).resolve().parent
OUT = HERE / "models" / "reservoir_falsifiability.json"
LAM = 1.0                 # damping; time in units of 1/lam, p_th = lam
G = 1.0                   # Stuart-Landau nonlinearity
DT = 0.1
SUBSTEPS = 10            # per input symbol
WASH, TRAIN, TEST = 200, 2000, 800


def narma10(u):
    y = np.zeros(len(u))
    for t in range(10, len(u)):
        y[t] = (0.3 * y[t-1] + 0.05 * y[t-1] * y[t-10:t].sum()
                + 1.5 * u[t-1] * u[t-10] + 0.1)
    return y


def reservoir(u, J, b, pfrac, c, isc=1.0, x0=None, y0=None):
    N = len(b)
    x = np.zeros(N) if x0 is None else x0.copy()
    y = np.zeros(N) if y0 is None else y0.copy()
    p = pfrac * LAM
    S = np.empty((len(u), 2 * N))
    for t, ut in enumerate(u):
        for _ in range(SUBSTEPS):
            r2 = x*x + y*y
            Jx = c * (J @ x); Jy = c * (J @ y)
            x = x + DT * ((-LAM + p) * x - G * r2 * x + Jx + b * isc * ut)
            y = y + DT * ((-LAM - p) * y - G * r2 * y + Jy)
        S[t] = np.concatenate([x, y])
    return S


def esn(u, seed, N=100, sr=0.9, isc=0.5, leak=0.5):
    rng = np.random.default_rng(seed)
    W = rng.standard_normal((N, N))
    W *= sr / max(abs(np.linalg.eigvals(W)))
    win = rng.uniform(-isc, isc, N)
    s = np.zeros(N); S = np.empty((len(u), N))
    for t, ut in enumerate(u):
        s = (1-leak)*s + leak*np.tanh(W @ s + win * ut)
        S[t] = s
    return S


def ridge(S, Y, reg=1e-6):
    A = np.hstack([S, np.ones((len(S), 1))])
    return np.linalg.solve(A.T@A + reg*np.eye(A.shape[1]), A.T@Y)


def predict(S, W):
    return np.hstack([S, np.ones((len(S), 1))]) @ W


def nrmse(yp, yt):
    return float(np.sqrt(np.mean((yp-yt)**2)) / (np.std(yt) + 1e-12))


def standardize(S, mu=None, sd=None):
    if mu is None:
        mu = S.mean(0); sd = S.std(0) + 1e-8
    return (S - mu) / sd, mu, sd


def evaluate(S, target):
    a, b2 = WASH, WASH + TRAIN
    Str, mu, sd = standardize(S[a:b2])
    Ste, _, _ = standardize(S[b2:b2+TEST], mu, sd)
    W = ridge(Str, target[a:b2])
    return nrmse(predict(Ste, W), target[b2:b2+TEST])


def memory_capacity(S, u, kmax=25):
    a, b2 = WASH, WASH + TRAIN
    Str, mu, sd = standardize(S[a:b2])
    Ste, _, _ = standardize(S[b2:b2+TEST], mu, sd)
    mc = 0.0
    for k in range(1, kmax+1):
        tgt = u[a-k:b2-k]
        W = ridge(Str, tgt)
        pr = predict(Ste, W); tt = u[b2-k:b2+TEST-k]
        r = np.corrcoef(pr, tt)[0, 1]
        mc += (r*r) if np.isfinite(r) else 0.0
    return float(mc)


def make_net(N, seed):
    rng = np.random.default_rng(seed)
    J = rng.standard_normal((N, N)); np.fill_diagonal(J, 0)
    J /= max(abs(np.linalg.eigvals(J)))      # spectral radius 1
    b = rng.standard_normal(N)
    return J, b


def linear_baseline(u, target, lags=16):
    T = len(u)
    S = np.zeros((T, lags))
    for k in range(lags):
        S[k:, k] = u[:T-k]
    return evaluate(S, target)


def main():
    from collections import defaultdict
    SEEDS = [0, 1, 2, 3, 4]
    N = 50
    PFRACS = [0.0, 0.5, 0.8, 0.95]
    CS = [0.0, 0.3, 0.6]
    ISCS = [0.5, 1.0, 2.0, 4.0]          # input scaling: lets the cubic engage

    data = {}
    for seed in SEEDS:
        rng = np.random.default_rng(1000 + seed)
        u = rng.uniform(0, 0.5, WASH + TRAIN + TEST)
        data[seed] = (u, narma10(u), *make_net(N, seed))

    rows = []
    for seed in SEEDS:
        u, y, J, b = data[seed]
        for pf in PFRACS:
            for c in CS:
                for isc in ISCS:
                    nr = evaluate(reservoir(u, J, b, pf, c, isc), y)
                    rows.append({"seed": seed, "pfrac": pf, "c": c, "isc": isc,
                                 "narma_nrmse": round(nr, 4)})

    def best_mean(filt):                  # per-seed best, then mean across seeds
        vals = [min(r["narma_nrmse"] for r in rows if r["seed"] == s and filt(r))
                for s in SEEDS]
        return float(np.mean(vals))

    best_overall = best_mean(lambda r: True)
    best_c0 = best_mean(lambda r: r["c"] == 0.0)
    best_cpos = best_mean(lambda r: r["c"] > 0)

    agg = defaultdict(list)
    for r in rows:
        agg[(r["pfrac"], r["c"], r["isc"])].append(r["narma_nrmse"])
    best_key = min(agg, key=lambda k: float(np.mean(agg[k])))
    best_res = {"pfrac": best_key[0], "c": best_key[1], "isc": best_key[2],
                "narma_nrmse": round(float(np.mean(agg[best_key])), 4)}
    grid = {f"pfrac{pf}_c{c}": round(min(float(np.mean(agg[(pf, c, i)])) for i in ISCS), 4)
            for pf in PFRACS for c in CS}

    # memory capacity at the best config
    best_mc = round(float(np.mean([memory_capacity(
        reservoir(data[s][0], data[s][2], data[s][3], *best_key), data[s][0])
        for s in SEEDS])), 2)

    # ESP at a representative sub-threshold config (pfrac 0.8, c 0.3, isc 1)
    esp_v = []
    for s in SEEDS:
        u, y, J, b = data[s]
        rng = np.random.default_rng(7000 + s)
        x0 = rng.standard_normal(N) * 0.5; y0 = rng.standard_normal(N) * 0.5
        SA = reservoir(u[:400], J, b, 0.8, 0.3, 1.0, x0, y0)
        SB = reservoir(u[:400], J, b, 0.8, 0.3, 1.0, -x0, -y0)
        d0 = np.linalg.norm(SA[0]-SB[0]); dT = np.linalg.norm(SA[-1]-SB[-1])
        esp_v.append(dT / (d0 + 1e-12))
    esp = float(np.mean(esp_v))

    # tuned ESN reference (fair strong baseline)
    esnm = min(float(np.mean([evaluate(esn(data[s][0], s, N=2*N, sr=sr, leak=lk),
                                       data[s][1]) for s in SEEDS]))
               for sr in (0.7, 0.9, 1.1) for lk in (0.3, 0.6, 1.0))
    # linear delay-line baseline (best lag count)
    lin = min(float(np.mean([linear_baseline(data[s][0], data[s][1], lags=L)
                             for s in SEEDS])) for L in (12, 16, 24))

    # resolve predictions
    p1 = int(esp < 0.01)                                   # echo-state property
    p2 = int((best_c0 - best_cpos) / best_c0 <= 0.05)      # coupling no advantage
    p3 = int((lin - best_overall) / lin > 0.10)            # beats linear by >10%

    res = {
        "config": {"N": N, "seeds": SEEDS, "pfracs": PFRACS, "cs": CS,
                   "lam": LAM, "g": G},
        "narma_nrmse_grid_mean": grid,
        "best_reservoir_mean_nrmse": round(best_overall, 4),
        "best_reservoir_cfg": best_res,
        "best_uncoupled_c0_nrmse": round(best_c0, 4),
        "best_coupled_cpos_nrmse": round(best_cpos, 4),
        "linear_baseline_nrmse": round(lin, 4),
        "esn_reference_nrmse": round(esnm, 4),
        "best_memory_capacity": round(best_mc, 2),
        "esp_final_over_initial_divergence": round(esp, 5),
        "resolved": {"echo_state_property_y": p1,
                     "coupling_no_advantage_y": p2,
                     "beats_linear_baseline_y": p3},
        "verdict": ("coupled sub-threshold parametron reservoir: "
                    + ("HAS ESP; " if p1 else "ESP FAILS; ")
                    + ("coupling does NOT help; " if p2 else "coupling HELPS; ")
                    + ("beats linear baseline" if p3 else "does NOT beat a linear filter")),
    }
    OUT.write_text(json.dumps(res, indent=2) + "\n")
    print(json.dumps(res, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
