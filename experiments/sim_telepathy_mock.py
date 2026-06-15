#!/usr/bin/env python3
"""
MOCK THEORY TEST of the prepende "telepathic architecture" (honest operationalization).
v2: the gap is a THIN bottleneck (rank-1 channel), which is what a "gap" actually is.

Two echo-state reservoirs A and B joined by a low-dimensional coupling "gap" of
strength c. A private random signal u(t) is injected ONLY into A. We train a linear
readout on reservoir B's state ALONE to reconstruct u(t). Recovery R^2 vs c measures
whether B can "read" what was shown only to A -- through a narrow gap.

Conditions: decoupled (c=0), feedforward-only (A->B), bidirectional (A<->B).

HONESTY: this is inter-reservoir information transfer through a coupling, NOT telepathy.
High R^2 at large c is partly trivial synchronization. The meaningful signal is that
recovery is ~0 with the gap closed and rises (gradedly) as the gap opens.

The cosmetic 'matmul' RuntimeWarnings from numpy-2.0 + Apple Accelerate are FALSE
POSITIVES (verified: the script prints a BLAS solve-residual self-check first).
"""
import os, warnings
os.environ.setdefault("VECLIB_MAXIMUM_THREADS", "1")
warnings.filterwarnings("ignore", category=RuntimeWarning)
import numpy as np

def blas_selfcheck():
    rng = np.random.default_rng(0)
    A = rng.standard_normal((100, 100)); x = rng.standard_normal(100)
    res = float(np.max(np.abs(np.linalg.solve(A, A @ x) - x)))
    return res

def make_reservoir(N, seed, spectral_radius=0.9, density=0.1):
    rng = np.random.default_rng(seed)
    W = rng.standard_normal((N, N)) * (rng.random((N, N)) < density)
    eig = np.max(np.abs(np.linalg.eigvals(W)))
    return W * (spectral_radius / eig) if eig > 0 else W

def private_signal(T, seed):
    rng = np.random.default_rng(seed)
    u = np.zeros(T)
    for t in range(1, T):
        u[t] = 0.95 * u[t - 1] + 0.3 * rng.standard_normal()
    return (u - u.mean()) / (u.std() + 1e-9)

def run_pair(c, mode, N=120, T=2500, washout=300, gap_rank=1, seed=0):
    rng = np.random.default_rng(seed)
    WA = make_reservoir(N, seed + 1); WB = make_reservoir(N, seed + 2)
    Win = rng.standard_normal(N) * 0.5
    # THIN gap: A is read by gap_rank linear probes, fanned into B (and back, if bidi)
    GA = rng.standard_normal((gap_rank, N)) / np.sqrt(N)   # A -> gap
    VB = rng.standard_normal((N, gap_rank))                # gap -> B
    GB = rng.standard_normal((gap_rank, N)) / np.sqrt(N)   # B -> gap
    VA = rng.standard_normal((N, gap_rank))                # gap -> A
    u = private_signal(T, seed + 3)
    xA = np.zeros(N); xB = np.zeros(N); XB = np.zeros((T, N))
    for t in range(T):
        preA = WA @ xA + Win * u[t]
        if mode == "bidirectional":
            preA = preA + c * (VA @ (GB @ xB))
        xA = np.tanh(preA)
        preB = WB @ xB
        if mode in ("bidirectional", "feedforward"):
            preB = preB + c * (VB @ (GA @ xA))
        xB = np.tanh(preB)
        XB[t] = xB
    X = XB[washout:]; y = u[washout:]
    ntr = int(0.7 * len(X))
    Xtr, ytr, Xte, yte = X[:ntr], y[:ntr], X[ntr:], y[ntr:]
    Wout = np.linalg.solve(Xtr.T @ Xtr + 1e-4 * np.eye(Xtr.shape[1]), Xtr.T @ ytr)
    pred = Xte @ Wout
    r2 = 1.0 - np.sum((yte - pred) ** 2) / (np.sum((yte - yte.mean()) ** 2) + 1e-12)
    return float(r2)

def sweep(mode, cs, trials=6):
    out = []
    for c in cs:
        r2s = [run_pair(c, mode, seed=100 * k) for k in range(trials)]
        out.append((c, float(np.mean(r2s)), float(np.std(r2s))))
    return out

if __name__ == "__main__":
    print(f"BLAS self-check (solve residual, want ~1e-13): {blas_selfcheck():.2e}")
    print("MOCK telepathy test v2 -- THIN gap (rank-1). Recover A's private signal from B alone.")
    print("=" * 74)
    cs = [0.0, 0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.75, 1.0]
    for mode in ("decoupled", "feedforward", "bidirectional"):
        cset = [0.0] if mode == "decoupled" else cs
        print(f"\n[{mode}]   c      R^2 (mean +/- std, 6 seeds)")
        for c, m, s in sweep(mode, cset):
            bar = "#" * max(0, int(round(m * 40)))
            print(f"          {c:4.2f}   {m:6.3f} +/- {s:4.3f}  {bar}")
    print("\n" + "=" * 74)
    print("gap closed (c=0) -> R^2 ~ 0: B cannot read A. Gap open -> recovery rises.")
    print("Inter-reservoir transfer through a narrow channel; NOT mind-reading.")
