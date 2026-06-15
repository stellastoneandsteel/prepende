#!/usr/bin/env python3
"""
MOCK telepathy test v3 -- EASY vs HARD regime, to fairly score the proponent's sigmoid prediction.

Same setup as v2 (thin rank-1 gap; private signal u(t) into A only; recover u from B
alone; R^2 vs coupling c). NEW: per-step internal process noise of amplitude `noise`
in both reservoirs. At low c the coupling signal is buried under noise -> low recovery;
as c opens the gap, signal climbs out -> graded curve. High c may synchronize/saturate.

noise=0.0 reproduces the easy-regime STEP. noise>0 should produce a graded/sigmoidal
rise (and possibly a high-c rolloff) -- exactly what the proponent predicted for a hard world.

Cosmetic numpy-2.0/Accelerate matmul warnings are false positives (BLAS self-check below).
"""
import os, warnings
os.environ.setdefault("VECLIB_MAXIMUM_THREADS", "1")
warnings.filterwarnings("ignore", category=RuntimeWarning)
import numpy as np

def blas_selfcheck():
    rng = np.random.default_rng(0); A = rng.standard_normal((100, 100)); x = rng.standard_normal(100)
    return float(np.max(np.abs(np.linalg.solve(A, A @ x) - x)))

def make_reservoir(N, seed, sr=0.9, density=0.1):
    rng = np.random.default_rng(seed)
    W = rng.standard_normal((N, N)) * (rng.random((N, N)) < density)
    eig = np.max(np.abs(np.linalg.eigvals(W)))
    return W * (sr / eig) if eig > 0 else W

def private_signal(T, seed):
    rng = np.random.default_rng(seed); u = np.zeros(T)
    for t in range(1, T):
        u[t] = 0.95 * u[t - 1] + 0.3 * rng.standard_normal()
    return (u - u.mean()) / (u.std() + 1e-9)

def statics(N, seed):
    rng = np.random.default_rng(seed)
    return dict(
        WA=make_reservoir(N, seed + 1), WB=make_reservoir(N, seed + 2),
        Win=rng.standard_normal(N) * 0.5,
        GA=rng.standard_normal((1, N)) / np.sqrt(N), VB=rng.standard_normal((N, 1)),
        GB=rng.standard_normal((1, N)) / np.sqrt(N), VA=rng.standard_normal((N, 1)),
        u=private_signal(2000, seed + 3), N=N)

def run(st, c, mode, noise, washout=300):
    N = st["N"]; T = len(st["u"]); u = st["u"]
    nrng = np.random.default_rng((hash((c, mode, round(noise, 3), N)) & 0xFFFFFFFF))
    xA = np.zeros(N); xB = np.zeros(N); XB = np.zeros((T, N))
    for t in range(T):
        preA = st["WA"] @ xA + st["Win"] * u[t]
        if mode == "bidirectional":
            preA = preA + c * (st["VA"] @ (st["GB"] @ xB))
        xA = np.tanh(preA + noise * nrng.standard_normal(N))
        preB = st["WB"] @ xB
        if mode in ("bidirectional", "feedforward"):
            preB = preB + c * (st["VB"] @ (st["GA"] @ xA))
        xB = np.tanh(preB + noise * nrng.standard_normal(N))
        XB[t] = xB
    X = XB[washout:]; y = u[washout:]; ntr = int(0.7 * len(X))
    W = np.linalg.solve(X[:ntr].T @ X[:ntr] + 1e-4 * np.eye(N), X[:ntr].T @ y[:ntr])
    pred = X[ntr:] @ W
    return float(1.0 - np.sum((y[ntr:] - pred) ** 2) / (np.sum((y[ntr:] - y[ntr:].mean()) ** 2) + 1e-12))

def sweep(mode, noise, cs, seeds=5):
    sts = [statics(120, 100 * k) for k in range(seeds)]
    res = []
    for c in cs:
        vals = [run(st, c, mode, noise) for st in sts]
        res.append((c, float(np.mean(vals)), float(np.std(vals))))
    return res

if __name__ == "__main__":
    print(f"BLAS self-check (want ~1e-13): {blas_selfcheck():.2e}")
    cs = [0.0, 0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.75, 1.0]
    for noise in (0.0, 0.5):
        tag = "EASY (noise=0.0)" if noise == 0.0 else "HARD (noise=0.5)"
        print("\n" + "=" * 76 + f"\nREGIME: {tag}\n" + "=" * 76)
        for mode in ("feedforward", "bidirectional"):
            print(f"\n[{mode}]   c      R^2 (mean +/- std, 5 seeds)")
            for c, m, s in sweep(mode, noise, cs):
                bar = "#" * max(0, int(round(m * 40)))
                print(f"          {c:4.2f}   {m:6.3f} +/- {s:4.3f}  {bar}")
    print("\n" + "=" * 76)
    print("If HARD shows a graded rise (and a high-c dip), the proponent's sigmoid+breakdown was")
    print("right for a harder world; the EASY step just had too much signal-to-noise.")
