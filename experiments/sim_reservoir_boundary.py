#!/usr/bin/env python3
"""sim_reservoir_boundary.py — Batch 5: where does the parametron reservoir earn its keep?

Batch 4 (sim_reservoir_falsifiability.py) found the coupled sub-threshold parametron
reservoir is "valid but weak": it has the echo-state property and coupling helps ~12%,
but a *trained linear delay-line* readout BEATS it on NARMA10 (~0.38 vs ~0.52 NRMSE).
NARMA10 is dominated by short-memory, near-linear structure, so a linear filter does well
and any reservoir edge is hidden.

Boundary-mapping hypothesis: a physical reservoir should only earn its keep on a task that
is *nonlinear in its memory* — something a linear readout of a linear delay line
structurally CANNOT compute. The canonical such task is temporal parity (XOR of delayed,
binarized inputs). If the reservoir wins big on XOR while losing on NARMA10, that maps the
boundary: the parametron fabric is useful exactly where linear filtering fails.

Task — temporal XOR:
    u(t) ~ U(0, 0.5);  b(t) = 1 if u(t) > 0.25 else 0
    target y(t) = b(t-D1) XOR b(t-D2)  in {-1, +1}     (D1, D2 = 2, 4)
A linear delay-line + linear readout cannot represent XOR -> should sit near chance (50%).
A nonlinear reservoir can -> should exceed it.

Methodology (stricter than Batch 4 — no test-set hyperparameter leakage):
    washout / TRAIN / VAL / TEST splits. For every method, hyperparameters are selected on
    VAL accuracy, and the winning config is reported on the held-out TEST split. 5 seeds.

Compared:
    - parametron reservoir   (coupled sub-threshold DPO envelope model; isc/pfrac/c swept)
    - linear delay-line      (trained ridge readout; lag count swept) — the NARMA10 winner
    - tanh ESN reference     (strong reservoir reference; sr/leak swept)

Metric: classification accuracy (ridge readout to +/-1, thresholded at 0). Chance = 0.5.

Pre-registered before this scored run (see experiments/predictions.jsonl):
    primary  reservoir_test_acc - linear_test_acc >= 0.15   (reservoir decisively wins)
    control  linear_test_acc < 0.60                          (task is genuinely nonlinear)

Reservoir dynamics are identical to Batch 4 (continuity). Pure numpy.

Usage:  python3 sim_reservoir_boundary.py            # full scored run -> writes results JSON
        python3 sim_reservoir_boundary.py --smoke    # tiny executability check, no scoring
"""
import json
import sys
from pathlib import Path
import numpy as np

HERE = Path(__file__).resolve().parent
OUT = HERE / "data" / "reservoir_boundary.json"
LAM = 1.0                 # damping; time in units of 1/lam, p_th = lam
G = 1.0                   # Stuart-Landau nonlinearity
DT = 0.1
SUBSTEPS = 10             # per input symbol
WASH, TRAIN, VAL, TEST = 200, 2000, 800, 800
D1, D2 = 2, 4             # XOR delays
THRESH = 0.25            # input binarization threshold (median of U(0,0.5))


def xor_task(u):
    """b(t)=1[u>THRESH]; target = b(t-D1) XOR b(t-D2) in {-1,+1}."""
    b = (u > THRESH).astype(int)
    y = np.zeros(len(u))
    for t in range(max(D1, D2), len(u)):
        y[t] = 1.0 if (b[t - D1] ^ b[t - D2]) else -1.0
    return y


CLIP = 1.0e3  # physical DPO amplitudes are O(1); this only contains explicit-Euler
              # runaway on overdriven (high-isc) configs so they fail cleanly instead
              # of overflowing. Selected (stable) configs never reach it -> identical
              # dynamics to Batch 4's sim_reservoir_falsifiability.py.


def reservoir(u, J, b, pfrac, c, isc=1.0, x0=None, y0=None):
    N = len(b)
    x = np.zeros(N) if x0 is None else x0.copy()
    y = np.zeros(N) if y0 is None else y0.copy()
    p = pfrac * LAM
    S = np.empty((len(u), 2 * N))
    with np.errstate(all="ignore"):
        for t, ut in enumerate(u):
            for _ in range(SUBSTEPS):
                r2 = x * x + y * y
                Jx = c * (J @ x); Jy = c * (J @ y)
                x = x + DT * ((-LAM + p) * x - G * r2 * x + Jx + b * isc * ut)
                y = y + DT * ((-LAM - p) * y - G * r2 * y + Jy)
                x = np.clip(x, -CLIP, CLIP); y = np.clip(y, -CLIP, CLIP)
            S[t] = np.concatenate([x, y])
    return S


def esn(u, seed, N=100, sr=0.9, isc=0.5, leak=0.5):
    rng = np.random.default_rng(seed)
    W = rng.standard_normal((N, N))
    W *= sr / max(abs(np.linalg.eigvals(W)))
    win = rng.uniform(-isc, isc, N)
    s = np.zeros(N); S = np.empty((len(u), N))
    for t, ut in enumerate(u):
        s = (1 - leak) * s + leak * np.tanh(W @ s + win * ut)
        S[t] = s
    return S


def ridge(S, Y, reg=1e-6):
    A = np.hstack([S, np.ones((len(S), 1))])
    with np.errstate(all="ignore"):
        return np.linalg.solve(A.T @ A + reg * np.eye(A.shape[1]), A.T @ Y)


def predict(S, W):
    return np.hstack([S, np.ones((len(S), 1))]) @ W


def standardize(S, mu=None, sd=None):
    if mu is None:
        mu = S.mean(0); sd = S.std(0) + 1e-8
    return (S - mu) / sd, mu, sd


def _splits(S, target):
    a = WASH
    b2 = WASH + TRAIN
    c2 = WASH + TRAIN + VAL
    d2 = WASH + TRAIN + VAL + TEST
    Str, mu, sd = standardize(S[a:b2])
    Sva, _, _ = standardize(S[b2:c2], mu, sd)
    Ste, _, _ = standardize(S[c2:d2], mu, sd)
    return (Str, target[a:b2]), (Sva, target[b2:c2]), (Ste, target[c2:d2])


def acc_train_eval(S, target):
    """Train ridge on TRAIN, return (val_acc, test_acc) as a sign-classification."""
    (Str, ytr), (Sva, yva), (Ste, yte) = _splits(S, target)
    W = ridge(Str, ytr)
    va = float(np.mean(np.sign(predict(Sva, W)) == yva))
    te = float(np.mean(np.sign(predict(Ste, W)) == yte))
    return va, te


def make_net(N, seed):
    rng = np.random.default_rng(seed)
    J = rng.standard_normal((N, N)); np.fill_diagonal(J, 0)
    J /= max(abs(np.linalg.eigvals(J)))      # spectral radius 1
    b = rng.standard_normal(N)
    return J, b


def linear_delayline(u, lags):
    T = len(u)
    S = np.zeros((T, lags))
    for k in range(lags):
        S[k:, k] = u[:T - k]
    return S


def main(smoke=False):
    if smoke:
        seeds = [0]; N = 8
        pfracs = [0.8]; cs = [0.3]; iscs = [2.0]
        lagset = [6]; esn_grid = [(0.9, 0.6)]
        global WASH, TRAIN, VAL, TEST
        WASH, TRAIN, VAL, TEST = 40, 120, 60, 60
    else:
        seeds = [0, 1, 2, 3, 4]; N = 50
        pfracs = [0.5, 0.8, 0.95]; cs = [0.0, 0.3, 0.6]; iscs = [1.0, 2.0, 4.0]
        lagset = [6, 8, 12, 16]; esn_grid = [(0.9, 0.3), (0.9, 0.6), (1.1, 0.6)]

    total = WASH + TRAIN + VAL + TEST
    data = {}
    for s in seeds:
        rng = np.random.default_rng(1000 + s)
        u = rng.uniform(0, 0.5, total)
        data[s] = (u, xor_task(u), *make_net(N, s))

    # ---- parametron reservoir: select (pfrac,c,isc) on VAL, report TEST ----
    res_cfg_scores = {}
    for pf in pfracs:
        for c in cs:
            for isc in iscs:
                vas, tes = [], []
                for s in seeds:
                    u, y, J, b = data[s]
                    va, te = acc_train_eval(reservoir(u, J, b, pf, c, isc), y)
                    vas.append(va); tes.append(te)
                res_cfg_scores[(pf, c, isc)] = (float(np.mean(vas)), float(np.mean(tes)),
                                                tes)
    best_res_cfg = max(res_cfg_scores, key=lambda k: res_cfg_scores[k][0])
    res_val, res_test, res_test_perseed = res_cfg_scores[best_res_cfg]

    # coupling contrast at fixed best (pfrac,isc): c=0 vs best c>0, selected on VAL
    pf0, c0sel, isc0 = best_res_cfg
    c0_val = np.mean([acc_train_eval(reservoir(*[data[s][0], data[s][2], data[s][3]],
                                               pf0, 0.0, isc0), data[s][1])[0]
                      for s in seeds])
    cpos = [k for k in res_cfg_scores if k[2] == isc0 and k[0] == pf0 and k[1] > 0]
    best_cpos = max(cpos, key=lambda k: res_cfg_scores[k][0]) if cpos else best_res_cfg
    cpos_val = res_cfg_scores[best_cpos][0]

    # ---- linear delay-line: select lags on VAL, report TEST ----
    lin_scores = {}
    for L in lagset:
        vas, tes = [], []
        for s in seeds:
            u, y, _, _ = data[s]
            va, te = acc_train_eval(linear_delayline(u, L), y)
            vas.append(va); tes.append(te)
        lin_scores[L] = (float(np.mean(vas)), float(np.mean(tes)))
    best_lin_L = max(lin_scores, key=lambda k: lin_scores[k][0])
    lin_val, lin_test = lin_scores[best_lin_L]

    # ---- tanh ESN reference: select (sr,leak) on VAL, report TEST ----
    esn_scores = {}
    for (sr, lk) in esn_grid:
        vas, tes = [], []
        for s in seeds:
            u, y, _, _ = data[s]
            va, te = acc_train_eval(esn(u, s, N=2 * N, sr=sr, leak=lk), y)
            vas.append(va); tes.append(te)
        esn_scores[(sr, lk)] = (float(np.mean(vas)), float(np.mean(tes)))
    best_esn = max(esn_scores, key=lambda k: esn_scores[k][0])
    esn_val, esn_test = esn_scores[best_esn]

    # ---- resolve pre-registered predictions ----
    margin = res_test - lin_test
    primary_y = int(margin >= 0.15)
    control_y = int(lin_test < 0.60)

    res = {
        "batch": 5,
        "task": "temporal-XOR  y(t)=b(t-%d) XOR b(t-%d), b=1[u>%.2f]" % (D1, D2, THRESH),
        "config": {"N": N, "seeds": seeds, "wash_train_val_test":
                   [WASH, TRAIN, VAL, TEST], "selection": "hyperparams chosen on VAL acc"},
        "parametron_reservoir": {
            "best_cfg": {"pfrac": best_res_cfg[0], "c": best_res_cfg[1],
                         "isc": best_res_cfg[2]},
            "val_acc": round(res_val, 4), "test_acc": round(res_test, 4),
            "test_acc_per_seed": [round(t, 4) for t in res_test_perseed],
        },
        "linear_delayline": {"best_lags": best_lin_L, "val_acc": round(lin_val, 4),
                             "test_acc": round(lin_test, 4)},
        "esn_reference": {"best_sr_leak": list(best_esn), "val_acc": round(esn_val, 4),
                          "test_acc": round(esn_test, 4)},
        "coupling_contrast_val": {"c0_acc": round(float(c0_val), 4),
                                  "best_cpos_acc": round(float(cpos_val), 4)},
        "reservoir_minus_linear_test_acc": round(float(margin), 4),
        "resolved": {"reservoir_beats_linear_by_15pts_y": primary_y,
                     "linear_near_chance_below_0.60_y": control_y},
        "verdict": ("temporal-XOR boundary: reservoir test acc %.3f vs linear %.3f "
                    "(ESN %.3f). " % (res_test, lin_test, esn_test)
                    + ("Reservoir DECISIVELY beats linear (>=15pts); "
                       if primary_y else "Reservoir does NOT clear +15pts vs linear; ")
                    + ("linear is near chance -> task is genuinely nonlinear."
                       if control_y else "linear is above chance -> task not fully nonlinear.")),
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
