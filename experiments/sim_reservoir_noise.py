#!/usr/bin/env python3
"""sim_reservoir_noise.py — Batch 6: does the parametron reservoir's XOR advantage
survive noise?

Batch 5 found the coupled sub-threshold parametron reservoir beats a trained linear
delay-line on temporal-XOR by ~20 points (0.68 vs 0.48-chance) — a task a linear readout
structurally cannot compute. But a *physical* reservoir is noisy. If that advantage
collapses under realistic measurement noise, it is not a real indicator. This batch maps
the noise boundary.

Noise model (fair — applied identically to BOTH methods): Gaussian readout-feature noise.
After standardizing each method's feature matrix (reservoir states, or the linear
delay-line lags) we add N(0, sigma) in units of feature std, on train AND test, and the
ridge readout is retrained at each noise level (the readout adapts to its noise floor).
This models a noisy ADC / measurement on whatever the readout sees, with no method
advantaged. sigma is swept 0 -> 1.0.

Hyperparameters are selected ONCE on the clean (sigma=0) validation split (Batch-5 grids),
then frozen; the sweep measures how that fixed system degrades. 5 seeds.

Pre-registered before this scored run (see experiments/predictions.jsonl):
  primary    margin(sigma=0.3) >= 0.10   advantage survives MODERATE noise
  boundary   margin(sigma=1.0)  < 0.10   advantage erodes under HEAVY noise (has a limit)
  (margin = reservoir_test_acc - linear_test_acc)

Reservoir dynamics identical to Batch 4/5. Pure numpy.

Usage:  python3 sim_reservoir_noise.py            # full scored run
        python3 sim_reservoir_noise.py --smoke    # tiny executability check
"""
import json
import sys
from pathlib import Path
import numpy as np

HERE = Path(__file__).resolve().parent
OUT = HERE / "data" / "reservoir_noise.json"
LAM = 1.0
G = 1.0
DT = 0.1
SUBSTEPS = 10
WASH, TRAIN, VAL, TEST = 200, 2000, 800, 800
D1, D2 = 2, 4
THRESH = 0.25
CLIP = 1.0e3
SIGMAS = [0.0, 0.1, 0.2, 0.3, 0.5, 1.0]


def xor_task(u):
    b = (u > THRESH).astype(int)
    y = np.zeros(len(u))
    for t in range(max(D1, D2), len(u)):
        y[t] = 1.0 if (b[t - D1] ^ b[t - D2]) else -1.0
    return y


def reservoir(u, J, b, pfrac, c, isc=1.0):
    N = len(b)
    x = np.zeros(N); y = np.zeros(N)
    p = pfrac * LAM
    S = np.empty((len(u), 2 * N))
    with np.errstate(all="ignore"):
        for t, ut in enumerate(u):
            for _ in range(SUBSTEPS):
                r2 = x * x + y * y
                x = x + DT * ((-LAM + p) * x - G * r2 * x + c * (J @ x) + b * isc * ut)
                y = y + DT * ((-LAM - p) * y - G * r2 * y + c * (J @ y))
                x = np.clip(x, -CLIP, CLIP); y = np.clip(y, -CLIP, CLIP)
            S[t] = np.concatenate([x, y])
    return S


def esn(u, seed, N=100, sr=0.9, isc=0.5, leak=0.5):
    rng = np.random.default_rng(seed)
    W = rng.standard_normal((N, N))
    with np.errstate(all="ignore"):
        W *= sr / max(abs(np.linalg.eigvals(W)))
    win = rng.uniform(-isc, isc, N)
    s = np.zeros(N); S = np.empty((len(u), N))
    with np.errstate(all="ignore"):
        for t, ut in enumerate(u):
            s = (1 - leak) * s + leak * np.tanh(W @ s + win * ut)
            S[t] = s
    return S


def ridge(S, Y, reg=1e-6):
    A = np.hstack([S, np.ones((len(S), 1))])
    with np.errstate(all="ignore"):
        return np.linalg.solve(A.T @ A + reg * np.eye(A.shape[1]), A.T @ Y)


def predict(S, W):
    with np.errstate(all="ignore"):
        return np.hstack([S, np.ones((len(S), 1))]) @ W


def make_net(N, seed):
    rng = np.random.default_rng(seed)
    J = rng.standard_normal((N, N)); np.fill_diagonal(J, 0)
    with np.errstate(all="ignore"):
        J /= max(abs(np.linalg.eigvals(J)))
    b = rng.standard_normal(N)
    return J, b


def linear_delayline(u, lags):
    T = len(u)
    S = np.zeros((T, lags))
    for k in range(lags):
        S[k:, k] = u[:T - k]
    return S


def _std_splits(S):
    a, b2, c2, d2 = WASH, WASH + TRAIN, WASH + TRAIN + VAL, WASH + TRAIN + VAL + TEST
    mu = S[a:b2].mean(0); sd = S[a:b2].std(0) + 1e-8
    return ((S[a:b2] - mu) / sd, (S[b2:c2] - mu) / sd, (S[c2:d2] - mu) / sd)


def _targ_splits(y):
    a, b2, c2, d2 = WASH, WASH + TRAIN, WASH + TRAIN + VAL, WASH + TRAIN + VAL + TEST
    return y[a:b2], y[b2:c2], y[c2:d2]


def clean_val_test_acc(S, y):
    Str, Sva, Ste = _std_splits(S)
    ytr, yva, yte = _targ_splits(y)
    W = ridge(Str, ytr)
    return (float(np.mean(np.sign(predict(Sva, W)) == yva)),
            float(np.mean(np.sign(predict(Ste, W)) == yte)))


def noisy_test_acc(S, y, sigma, rng):
    """Retrain readout on noisy train, evaluate on noisy test. Noise in feature-std units."""
    Str, Sva, Ste = _std_splits(S)
    ytr, yva, yte = _targ_splits(y)
    Str_n = Str + rng.normal(0, sigma, Str.shape)
    Ste_n = Ste + rng.normal(0, sigma, Ste.shape)
    W = ridge(Str_n, ytr)
    return float(np.mean(np.sign(predict(Ste_n, W)) == yte))


def main(smoke=False):
    global WASH, TRAIN, VAL, TEST
    if smoke:
        seeds = [0]; N = 8
        WASH, TRAIN, VAL, TEST = 40, 120, 60, 60
        pfracs, cs, iscs = [0.8], [0.3], [2.0]
        lagset, esn_grid = [6], [(0.9, 0.6)]
        sigmas = [0.0, 0.3, 1.0]
    else:
        seeds = [0, 1, 2, 3, 4]; N = 50
        pfracs, cs, iscs = [0.5, 0.8, 0.95], [0.0, 0.3, 0.6], [1.0, 2.0, 4.0]
        lagset, esn_grid = [6, 8, 12, 16], [(0.9, 0.3), (0.9, 0.6), (1.1, 0.6)]
        sigmas = SIGMAS

    total = WASH + TRAIN + VAL + TEST
    data = {}
    for s in seeds:
        rng = np.random.default_rng(1000 + s)
        u = rng.uniform(0, 0.5, total)
        data[s] = (u, xor_task(u), *make_net(N, s))

    # ---- select hyperparameters on CLEAN val (Batch-5 grids), freeze ----
    def res_val(cfg):
        return float(np.mean([clean_val_test_acc(
            reservoir(data[s][0], data[s][2], data[s][3], *cfg), data[s][1])[0]
            for s in seeds]))
    res_cfgs = [(pf, c, isc) for pf in pfracs for c in cs for isc in iscs]
    best_res = max(res_cfgs, key=res_val)
    lin_val = lambda L: float(np.mean([clean_val_test_acc(
        linear_delayline(data[s][0], L), data[s][1])[0] for s in seeds]))
    best_lin = max(lagset, key=lin_val)
    esn_val = lambda g: float(np.mean([clean_val_test_acc(
        esn(data[s][0], s, N=2 * N, sr=g[0], leak=g[1]), data[s][1])[0] for s in seeds]))
    best_esn = max(esn_grid, key=esn_val)

    # ---- precompute frozen-cfg feature matrices per seed ----
    res_S = {s: reservoir(data[s][0], data[s][2], data[s][3], *best_res) for s in seeds}
    lin_S = {s: linear_delayline(data[s][0], best_lin) for s in seeds}
    esn_S = {s: esn(data[s][0], s, N=2 * N, sr=best_esn[0], leak=best_esn[1]) for s in seeds}

    # ---- noise sweep (fixed RNG per seed for reproducibility) ----
    curve = {}
    for sigma in sigmas:
        r_acc = np.mean([noisy_test_acc(res_S[s], data[s][1], sigma,
                                        np.random.default_rng(9000 + s)) for s in seeds])
        l_acc = np.mean([noisy_test_acc(lin_S[s], data[s][1], sigma,
                                        np.random.default_rng(9100 + s)) for s in seeds])
        e_acc = np.mean([noisy_test_acc(esn_S[s], data[s][1], sigma,
                                        np.random.default_rng(9200 + s)) for s in seeds])
        curve[sigma] = {"reservoir": round(float(r_acc), 4),
                        "linear": round(float(l_acc), 4),
                        "esn": round(float(e_acc), 4),
                        "margin": round(float(r_acc - l_acc), 4)}

    margin_03 = curve[0.3]["margin"]
    margin_10 = curve[1.0]["margin"]
    primary_y = int(margin_03 >= 0.10)
    boundary_y = int(margin_10 < 0.10)

    res = {
        "batch": 6,
        "task": "temporal-XOR under readout-feature noise (Gaussian, feature-std units)",
        "config": {"N": N, "seeds": seeds, "sigmas": sigmas,
                   "selection": "hyperparams frozen at clean(sigma=0) VAL; readout "
                                "retrained per sigma; noise on train+test, both methods",
                   "frozen_cfg": {"reservoir": {"pfrac": best_res[0], "c": best_res[1],
                                                "isc": best_res[2]},
                                  "linear_lags": best_lin,
                                  "esn_sr_leak": list(best_esn)}},
        "noise_curve": {str(k): v for k, v in curve.items()},
        "margin_at_0.3": margin_03,
        "margin_at_1.0": margin_10,
        "resolved": {"advantage_survives_moderate_noise_y": primary_y,
                     "advantage_erodes_under_heavy_noise_y": boundary_y},
        "verdict": ("XOR advantage vs noise: margin %.3f (clean) -> %.3f (sigma0.3) -> "
                    "%.3f (sigma1.0). " % (curve[0.0]["margin"], margin_03, margin_10)
                    + ("Survives moderate noise (>=10pts at sigma0.3); "
                       if primary_y else "Does NOT survive moderate noise; ")
                    + ("erodes below 10pts under heavy noise (mapped upper limit)."
                       if boundary_y else "still >=10pts even at heavy noise (very robust).")),
    }
    if smoke:
        print("SMOKE OK — sim executes; (scored numbers suppressed)")
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, indent=2) + "\n")
    print(json.dumps(res, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(smoke="--smoke" in sys.argv))
