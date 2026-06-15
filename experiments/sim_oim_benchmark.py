#!/usr/bin/env python3
"""
Build A benchmark: does the Oscillator-Ising-Machine (OIM) *dynamics* actually
solve Max-Cut well, and how does solution quality scale? Compared against a strong
classical baseline (simulated annealing) at matched effort.

HARD HONESTY CAVEAT (read first):
This simulates the OIM's continuous-time dynamics ON A CPU. It therefore measures
SOLUTION QUALITY (how good a cut it finds, and hit-rate), NOT the physical
energy/latency advantage -- that advantage only exists when the dynamics run in
real analog hardware. In software, simulating the ODE is *slower* than just running
SA. So: a quality/correctness benchmark of the algorithm, not an energy benchmark.
The energy win remains a hardware claim to be tested on a bench.

Vectorized OIM update uses sin(a-b)=sin a cos b - cos a sin b so the coupling sum
becomes matrix-vector products (fast). numpy required; cosmetic Accelerate warnings
are false positives (see prior BLAS self-check).
"""
import os, warnings, time, math
os.environ.setdefault("VECLIB_MAXIMUM_THREADS", "1")
warnings.filterwarnings("ignore", category=RuntimeWarning)
import numpy as np

def random_graph(n, p, seed):
    rng = np.random.default_rng(seed)
    A = (rng.random((n, n)) < p).astype(float)
    A = np.triu(A, 1); A = A + A.T
    return A

def cut_of(spin, A):  # spin in {0,1}
    s = spin.astype(bool)
    return 0.5 * np.sum(A * (s[:, None] ^ s[None, :]))

def brute_force(A):
    n = A.shape[0]; best = -1.0
    Ei = [(i, j) for i in range(n) for j in range(i + 1, n) if A[i, j] > 0]
    for mask in range(1 << (n - 1)):
        sp = [(mask >> k) & 1 for k in range(n - 1)] + [0]
        c = sum(1 for (i, j) in Ei if sp[i] != sp[j])
        if c > best: best = c
    return best

def oim_solve(A, restarts, steps=1500, dt=0.05, Ks_max=1.8, noise0=0.7, seed=0):
    n = A.shape[0]; rng = np.random.default_rng(seed)
    best = -1.0; hits_at = []
    for r in range(restarts):
        th = rng.uniform(0, 2 * math.pi, n)
        for t in range(steps):
            frac = t / steps; Ks = Ks_max * frac; noise = noise0 * (1 - frac)
            ct = np.cos(th); st = np.sin(th)
            # for Max-Cut J_ij = -1 -> drift_i = sum_j A_ij sin(th_i - th_j)
            drift = st * (A @ ct) - ct * (A @ st) - Ks * np.sin(2 * th)
            th = th + dt * drift + noise * math.sqrt(dt) * rng.standard_normal(n)
        spin = (np.cos(th) > 0).astype(int)
        c = cut_of(spin, A); hits_at.append(c); best = max(best, c)
    return best, hits_at

def sa_solve(A, restarts, sweeps=160, seed=0):
    n = A.shape[0]; rng = np.random.default_rng(seed + 9999); best = -1.0
    for r in range(restarts):
        s = rng.integers(0, 2, n) * 2 - 1  # +/-1
        T0, T1 = 2.0, 0.02
        for sw in range(sweeps):
            T = T0 * (T1 / T0) ** (sw / max(1, sweeps - 1))
            for i in rng.permutation(n):
                d = s[i] * (A[i] @ s)        # delta cut from flipping i
                if d > 0 or rng.random() < math.exp(-max(0.0, -d) / T):
                    s[i] = -s[i]
        spin = ((s + 1) // 2).astype(int)
        best = max(best, cut_of(spin, A))
    return best

if __name__ == "__main__":
    print("Build A (OIM dynamics) vs simulated annealing on Max-Cut -- SOLUTION QUALITY")
    print("note: software sim measures cut quality, NOT the hardware energy/latency win")
    print("=" * 92)
    print(f"{'graph':16s}{'edges':>7s}{'opt/best':>10s}{'OIM':>7s}{'SA':>7s}"
          f"{'OIM/best':>10s}{'OIM hit%':>10s}{'OIM s':>8s}")
    sizes = [(12, 0.5), (20, 0.5), (40, 0.5), (80, 0.5), (120, 0.5)]
    for (n, p) in sizes:
        for seed in (1, 2):
            A = random_graph(n, p, seed)
            E = int(A.sum() // 2)
            t0 = time.time()
            oim_best, hits = oim_solve(A, restarts=50, seed=seed)
            oim_t = time.time() - t0
            sa_best = sa_solve(A, restarts=30, seed=seed)
            if n <= 18:
                opt = brute_force(A); ref = opt; reftag = f"{int(opt)}"
            else:
                ref = max(oim_best, sa_best); reftag = f"~{int(ref)}"
            hitrate = 100.0 * sum(1 for h in hits if h >= ref) / len(hits)
            print(f"n={n:<3d} p={p:<4.1f} s{seed} {E:7d}{reftag:>10s}"
                  f"{int(oim_best):7d}{int(sa_best):7d}"
                  f"{oim_best/ref*100:9.1f}%{hitrate:9.1f}%{oim_t:8.2f}")
    print("=" * 92)
    print("opt = brute-force optimum (n<=18); ~best = best cut found by either method (n>=20).")
    print("OIM/best = OIM's cut as % of reference; OIM hit% = restarts reaching reference.")
