#!/usr/bin/env python3
"""sim_reservoir_recover.py — Batch 8: where does the noise-fragility live, and can it
be recovered?

Batches 5-7 established a real result and an honest limit:
  - clean: the coupled sub-threshold parametron reservoir beats a trained linear
    delay-line on temporal-XOR by ~0.20 (a task a linear readout structurally cannot
    compute) — a genuine nonlinear-memory advantage.
  - noise: that advantage COLLAPSES under modest readout-feature noise (margin ~0.03 at
    sigma=0.3, Batch 6) and scaling N does NOT rescue it (Batch 7). Two optimistic bets
    lost, reported as losses.

This batch asks the diagnostic follow-up. If the fragility is a *measurement* problem —
additive noise on whatever the readout samples — then the standard fair fix is to read the
same features M times and average before the readout (effective noise std -> sigma/sqrt(M)).
Averaging is applied IDENTICALLY to the reservoir and the linear delay-line, so it advantages
neither: it cannot give a linear readout a computation it structurally lacks, it can only
strip measurement noise that was masking the reservoir's already-demonstrated capacity.

We test recovery at the noise level that killed the edge in Batch 6 (sigma=0.3):
  M=1   no averaging  -> reproduces Batch 6 (control)
  M=4   token averaging (effective sigma 0.15)
  M=16  real averaging (effective sigma 0.075)

Everything else is the Batch 6 regime, unchanged: same reservoir dynamics, same temporal
-XOR(D1=2,D2=4), same linear delay-line baseline, hyperparameters frozen on the clean
(sigma=0) validation split, ridge readout retrained per condition, noise on train AND test,
5 seeds. margin = reservoir_test_acc - linear_test_acc.

Pre-registered before this scored run (see experiments/predictions.jsonl):
  [B8-recover]  margin(M=16, sigma0.3) >= 0.10   averaging RECOVERS the advantage
  [B8-control]  margin(M=1,  sigma0.3)  < 0.10   single-shot still collapses (Batch 6 holds)
  [B8-dose]     margin(M=4,  sigma0.3)  < 0.10   token averaging is NOT enough (dose-response)

Pure numpy. Reservoir dynamics identical to Batch 4/5/6.

Usage:  python3 sim_reservoir_recover.py            # full scored run
        python3 sim_reservoir_recover.py --smoke    # tiny executability check
"""
import json
import sys
from pathlib import Path
import numpy as np

HERE = Path(__file__).resolve().parent
OUT = HERE / "data" / "reservoir_recover.json"
LAM = 1.0
G = 1.0
DT = 0.1
SUBSTEPS = 10
WASH, TRAIN, VAL, TEST = 200, 2000, 800, 800
D1, D2 = 2, 4
THRESH = 0.25
CLIP = 1.0e3
SIGMA = 0.3            # the noise level that killed the edge in Batch 6
MS = [1, 4, 16]        # measurement-averaging depth


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


def clean_val_acc(S, y):
    """Clean validation accuracy — used ONLY for frozen hyperparameter selection."""
    Str, Sva, Ste = _std_splits(S)
    ytr, yva, yte = _targ_splits(y)
    W = ridge(Str, ytr)
    return float(np.mean(np.sign(predict(Sva, W)) == yva))


def avg_noisy_test_acc(S, y, sigma, M, rng):
    """Measurement-averaged readout. M independent N(0,sigma) reads (feature-std units) of
    each standardized feature vector are AVERAGED before the ridge readout, on train AND
    test (effective noise std sigma/sqrt(M)). Readout retrained on the averaged-noisy train.
    Identical operator for reservoir and linear delay-line — advantages neither."""
    Str, _, Ste = _std_splits(S)
    ytr, _, yte = _targ_splits(y)

    def avg_reads(X):
        acc = np.zeros_like(X)
        for _ in range(M):
            acc += X + rng.normal(0, sigma, X.shape)
        return acc / M

    Str_n = avg_reads(Str)
    Ste_n = avg_reads(Ste)
    W = ridge(Str_n, ytr)
    return float(np.mean(np.sign(predict(Ste_n, W)) == yte))


def main(smoke=False):
    global WASH, TRAIN, VAL, TEST
    if smoke:
        seeds = [0]; N = 8
        WASH, TRAIN, VAL, TEST = 40, 120, 60, 60
        pfracs, cs, iscs = [0.8], [0.3], [2.0]
        lagset, esn_grid = [6], [(0.9, 0.6)]
        ms = [1, 4, 16]
    else:
        seeds = [0, 1, 2, 3, 4]; N = 50
        pfracs, cs, iscs = [0.5, 0.8, 0.95], [0.0, 0.3, 0.6], [1.0, 2.0, 4.0]
        lagset, esn_grid = [6, 8, 12, 16], [(0.9, 0.3), (0.9, 0.6), (1.1, 0.6)]
        ms = MS

    total = WASH + TRAIN + VAL + TEST
    data = {}
    for s in seeds:
        rng = np.random.default_rng(1000 + s)
        u = rng.uniform(0, 0.5, total)
        data[s] = (u, xor_task(u), *make_net(N, s))

    # ---- select hyperparameters on CLEAN val (Batch-5/6 grids), freeze ----
    def res_val(cfg):
        return float(np.mean([clean_val_acc(
            reservoir(data[s][0], data[s][2], data[s][3], *cfg), data[s][1]) for s in seeds]))
    res_cfgs = [(pf, c, isc) for pf in pfracs for c in cs for isc in iscs]
    best_res = max(res_cfgs, key=res_val)
    lin_val = lambda L: float(np.mean([clean_val_acc(
        linear_delayline(data[s][0], L), data[s][1]) for s in seeds]))
    best_lin = max(lagset, key=lin_val)
    esn_val = lambda g: float(np.mean([clean_val_acc(
        esn(data[s][0], s, N=2 * N, sr=g[0], leak=g[1]), data[s][1]) for s in seeds]))
    best_esn = max(esn_grid, key=esn_val)

    # ---- precompute frozen-cfg feature matrices per seed ----
    res_S = {s: reservoir(data[s][0], data[s][2], data[s][3], *best_res) for s in seeds}
    lin_S = {s: linear_delayline(data[s][0], best_lin) for s in seeds}
    esn_S = {s: esn(data[s][0], s, N=2 * N, sr=best_esn[0], leak=best_esn[1]) for s in seeds}

    # ---- clean (sigma=0) reference margin, for context ----
    clean = {}
    for s in seeds:
        clean[s] = None
    r_clean = np.mean([avg_noisy_test_acc(res_S[s], data[s][1], 0.0, 1,
                                          np.random.default_rng(8000 + s)) for s in seeds])
    l_clean = np.mean([avg_noisy_test_acc(lin_S[s], data[s][1], 0.0, 1,
                                          np.random.default_rng(8100 + s)) for s in seeds])

    # ---- measurement-averaging sweep at sigma=0.3 (fixed RNG per condition) ----
    sweep = {}
    for M in ms:
        r_acc = np.mean([avg_noisy_test_acc(res_S[s], data[s][1], SIGMA, M,
                         np.random.default_rng(9000 + 137 * M + s)) for s in seeds])
        l_acc = np.mean([avg_noisy_test_acc(lin_S[s], data[s][1], SIGMA, M,
                         np.random.default_rng(9100 + 137 * M + s)) for s in seeds])
        e_acc = np.mean([avg_noisy_test_acc(esn_S[s], data[s][1], SIGMA, M,
                         np.random.default_rng(9200 + 137 * M + s)) for s in seeds])
        sweep[M] = {"reservoir": round(float(r_acc), 4),
                    "linear": round(float(l_acc), 4),
                    "esn": round(float(e_acc), 4),
                    "margin": round(float(r_acc - l_acc), 4)}

    m_ctrl = sweep[1]["margin"]    # M=1   single-shot
    m_dose = sweep[4]["margin"]    # M=4   token averaging
    m_rec = sweep[16]["margin"]    # M=16  real averaging

    recover_y = int(m_rec >= 0.10)
    control_y = int(m_ctrl < 0.10)
    dose_y = int(m_dose < 0.10)

    res = {
        "batch": 8,
        "task": "temporal-XOR; measurement-averaging recovery at sigma=0.3 (Batch-6 regime)",
        "config": {"N": N, "seeds": seeds, "sigma": SIGMA, "Ms": ms,
                   "selection": "hyperparams frozen at clean(sigma=0) VAL; readout "
                                "retrained per (M); M reads averaged before readout, "
                                "identical for reservoir and linear delay-line",
                   "frozen_cfg": {"reservoir": {"pfrac": best_res[0], "c": best_res[1],
                                                "isc": best_res[2]},
                                  "linear_lags": best_lin,
                                  "esn_sr_leak": list(best_esn)}},
        "clean_reference": {"reservoir": round(float(r_clean), 4),
                            "linear": round(float(l_clean), 4),
                            "margin": round(float(r_clean - l_clean), 4)},
        "sweep_sigma0.3": {str(k): v for k, v in sweep.items()},
        "margin_M1": m_ctrl, "margin_M4": m_dose, "margin_M16": m_rec,
        "resolved": {"recover_y": recover_y, "control_y": control_y, "dose_y": dose_y},
        "verdict": (
            "Recovery at sigma=0.3 (clean margin %.3f): M=1 %.3f -> M=4 %.3f -> M=16 %.3f. "
            % (r_clean - l_clean, m_ctrl, m_dose, m_rec)
            + ("Measurement-averaging RECOVERS the edge (M=16 >= 0.10): the fragility "
               "lived in the readout, not the dynamics. " if recover_y
               else "Averaging does NOT recover the edge even at M=16: the loss is deeper "
                    "than measurement noise. ")
            + ("Single-shot still collapses (control holds). " if control_y
               else "Single-shot did NOT collapse — harness diverges from Batch 6, read with care. ")
            + ("Token M=4 is not enough (dose-response confirmed)." if dose_y
               else "Token M=4 already suffices — recovery is cheap, no steep dose-response.")),
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
