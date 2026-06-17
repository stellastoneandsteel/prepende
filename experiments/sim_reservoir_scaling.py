#!/usr/bin/env python3
"""sim_reservoir_scaling.py — Batch 7: does scale rescue the parametron reservoir's
noise-fragile XOR edge?

Batch 5: the coupled sub-threshold parametron reservoir beats a linear delay-line on
temporal-XOR by ~20 points. Batch 6: that edge collapses under ~10% readout noise (margin
+0.20 clean -> +0.03 at sigma=0.3). The natural rescue hypothesis from reservoir-computing
theory: a LARGER reservoir spreads the XOR-relevant signal across more features, so a ridge
readout averages independent per-feature noise down (SNR ~ sqrt of effective feature count)
and the advantage should return at scale.

This batch sweeps reservoir size N in {50, 100, 200, 400}, dynamics fixed at the Batch-5
optimum (pfrac=0.5, c=0.3, isc=2.0) so N is the only variable, and measures, per N:
  - clean temporal-XOR test accuracy (capacity)
  - noisy test accuracy at the Batch-6 collapse point sigma=0.3 (robustness)
versus the same trained linear delay-line baseline, with noise applied identically to both.
Method matches Batch 6: standardized features, readout retrained per condition, noise in
feature-std units, 5 seeds.

Pre-registered before this scored run (see experiments/predictions.jsonl):
  primary  margin(N=200, sigma=0.3) >= 0.10   scale RESTORES noise-robustness   (p=0.50)
  control  clean_acc(N=400) - clean_acc(N=50) >= 0.05   scale improves clean capacity (p=0.70)
  (margin = reservoir_test_acc - linear_test_acc)

Reservoir dynamics identical to Batch 4/5/6. Pure numpy. (ESN dropped — Batch 6 already
showed it collapses identically; here the question is reservoir-vs-linear under scale.)

Usage:  python3 sim_reservoir_scaling.py            # full scored run (~2-3 min)
        python3 sim_reservoir_scaling.py --smoke    # tiny executability check
"""
import json
import sys
from pathlib import Path
import numpy as np

HERE = Path(__file__).resolve().parent
OUT = HERE / "data" / "reservoir_scaling.json"
LAM = 1.0
G = 1.0
DT = 0.1
SUBSTEPS = 10
WASH, TRAIN, VAL, TEST = 200, 2000, 800, 800
D1, D2 = 2, 4
THRESH = 0.25
CLIP = 1.0e3
PFRAC, C, ISC = 0.5, 0.3, 2.0      # Batch-5 optimum, fixed so N is the only variable
SIGMA_NOISE = 0.3                  # the Batch-6 collapse point


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


def _targ(y):
    a, b2, c2, d2 = WASH, WASH + TRAIN, WASH + TRAIN + VAL, WASH + TRAIN + VAL + TEST
    return y[a:b2], y[b2:c2], y[c2:d2]


def clean_acc(S, y):
    Str, Sva, Ste = _std_splits(S); ytr, yva, yte = _targ(y)
    W = ridge(Str, ytr)
    return float(np.mean(np.sign(predict(Ste, W)) == yte))


def noisy_acc(S, y, sigma, rng):
    Str, Sva, Ste = _std_splits(S); ytr, yva, yte = _targ(y)
    W = ridge(Str + rng.normal(0, sigma, Str.shape), ytr)
    return float(np.mean(np.sign(predict(Ste + rng.normal(0, sigma, Ste.shape), W)) == yte))


def main(smoke=False):
    global WASH, TRAIN, VAL, TEST
    if smoke:
        seeds = [0]; Ns = [8, 16]
        WASH, TRAIN, VAL, TEST = 40, 120, 60, 60
        lagset = [6]
    else:
        seeds = [0, 1, 2, 3, 4]; Ns = [50, 100, 200, 400]
        lagset = [6, 8, 12, 16]

    total = WASH + TRAIN + VAL + TEST
    data = {}
    for s in seeds:
        rng = np.random.default_rng(1000 + s)
        u = rng.uniform(0, 0.5, total)
        data[s] = (u, xor_task(u))

    # linear baseline (N-independent): select lags on clean VAL
    def lin_val(L):
        accs = []
        for s in seeds:
            u, y = data[s]
            Str, Sva, Ste = _std_splits(linear_delayline(u, L)); ytr, yva, yte = _targ(y)
            W = ridge(Str, ytr); accs.append(float(np.mean(np.sign(predict(Sva, W)) == yva)))
        return float(np.mean(accs))
    best_lin = max(lagset, key=lin_val)
    lin_clean = float(np.mean([clean_acc(linear_delayline(data[s][0], best_lin), data[s][1])
                               for s in seeds]))
    lin_noisy = float(np.mean([noisy_acc(linear_delayline(data[s][0], best_lin), data[s][1],
                                         SIGMA_NOISE, np.random.default_rng(9100 + s))
                               for s in seeds]))

    curve = {}
    for N in Ns:
        cln, nzy = [], []
        for s in seeds:
            u, y = data[s]
            J, b = make_net(N, s)
            S = reservoir(u, J, b, PFRAC, C, ISC)
            cln.append(clean_acc(S, y))
            nzy.append(noisy_acc(S, y, SIGMA_NOISE, np.random.default_rng(9000 + s)))
        rc, rn = float(np.mean(cln)), float(np.mean(nzy))
        curve[N] = {"clean_acc": round(rc, 4), "noisy_acc": round(rn, 4),
                    "clean_margin": round(rc - lin_clean, 4),
                    "noisy_margin": round(rn - lin_noisy, 4)}

    margin_200_noisy = curve[Ns[2]]["noisy_margin"] if len(Ns) > 2 else curve[Ns[-1]]["noisy_margin"]
    clean_gain = curve[Ns[-1]]["clean_acc"] - curve[Ns[0]]["clean_acc"]
    primary_y = int(margin_200_noisy >= 0.10)
    control_y = int(clean_gain >= 0.05)

    res = {
        "batch": 7,
        "task": "temporal-XOR; reservoir size sweep at fixed dynamics; clean vs sigma=0.3 noise",
        "config": {"Ns": Ns, "seeds": seeds, "sigma_noise": SIGMA_NOISE,
                   "fixed_dynamics": {"pfrac": PFRAC, "c": C, "isc": ISC},
                   "linear_baseline": {"lags": best_lin, "clean_acc": round(lin_clean, 4),
                                       "noisy_acc": round(lin_noisy, 4)}},
        "scaling_curve": {str(k): v for k, v in curve.items()},
        "margin_at_N200_sigma0.3": round(float(margin_200_noisy), 4),
        "clean_acc_gain_N50_to_N400": round(float(clean_gain), 4),
        "resolved": {"scale_restores_noise_robustness_y": primary_y,
                     "scale_improves_clean_capacity_y": control_y},
        "verdict": ("scaling N: noisy(sigma0.3) margin at N=%d is %.3f; clean acc N=%d->N=%d "
                    "gains %+.3f. " % (Ns[2] if len(Ns) > 2 else Ns[-1], margin_200_noisy,
                                       Ns[0], Ns[-1], clean_gain)
                    + ("Scale RESTORES noise-robustness (>=0.10). "
                       if primary_y else "Scale does NOT restore noise-robustness (<0.10). ")
                    + ("Bigger reservoirs are cleanly better."
                       if control_y else "Clean capacity barely moves with size.")),
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
