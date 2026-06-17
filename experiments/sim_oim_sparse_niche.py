#!/usr/bin/env python3
"""oim_scale_niche.py — does the OIM stay competitive at scale on the right
problems, and does hybrid-digital (quantized) coupling survive?

Batch 9 (scalable coupling): vectorized phase-model OIM with SHIL binarization
vs a classical randomized greedy local-search baseline (an SA-class heuristic),
on SPARSE 3-regular graphs vs DENSE Erdos-Renyi p=0.5 graphs, N in {50..400}.
Metric: approximation ratio = OIM_best_cut / classical_best_cut.

Batch 10 (hybrid-digital coupling): weighted Max-Cut at N=100; quantize the
coupling matrix to {full, 4-bit, 2-bit, 1-bit sign} and measure quality
retention vs full precision.

OIM dynamics (gradient flow on E = sum_{i<j} J_ij cos(theta_i - theta_j), which
equals the Ising energy at binary phases, plus a SHIL binarizing term):
  dtheta_i/dt = sum_j J_ij sin(theta_i - theta_j) - Ks(t) sin(2 theta_i)
drives edges to anti-phase (a cut) and locks phases to 0/pi.
"""
import json, math
from pathlib import Path
import numpy as np

HERE = Path(__file__).resolve().parent
OUT = HERE / "models" / "oim_scale_niche.json"


def cut_value(A, s):
    return float((A * (s[:, None] != s[None, :])).sum() / 2.0)


def weighted_cut(W, s):
    return float((W * (s[:, None] != s[None, :])).sum() / 2.0)


def oim_solve(J, restarts, rng, steps=2500, dt=0.02, ks_max=1.0):
    N = J.shape[0]; best = -1; best_s = None
    for _ in range(restarts):
        th = rng.uniform(0, 2*math.pi, N)
        for t in range(steps):
            ks = ks_max * (t / steps)               # anneal the SHIL pump up
            c, sn = np.cos(th), np.sin(th)
            coup = sn * (J @ c) - c * (J @ sn)       # sum_j J_ij sin(th_i-th_j)
            th = th + dt * (coup - ks * np.sin(2*th))
        s = np.where(np.cos(th) >= 0, 1, -1)
        v = cut_value(J if J.dtype != float else (J != 0).astype(float), s) if False else None
        best = best  # placeholder
        # caller computes the cut against the real (possibly weighted) matrix
        yield s


def oim_best_cut(adj, restarts, rng, weighted=None, **kw):
    best = -1.0
    for s in oim_solve(adj, restarts, rng, **kw):
        v = weighted_cut(weighted, s) if weighted is not None else cut_value(adj, s)
        best = max(best, v)
    return best


def classical_best_cut(adj, restarts, rng, weighted=None):
    """Randomized greedy local search with restarts (SA-class classical baseline)."""
    M = weighted if weighted is not None else adj
    N = M.shape[0]; best = -1.0
    for _ in range(restarts):
        s = rng.choice([-1.0, 1.0], N)
        Ms = M @ s
        improved = True
        while improved:
            gain = s * Ms                  # flipping i changes cut by +gain_i (weighted)
            i = int(np.argmax(gain))
            if gain[i] <= 1e-9:
                improved = False; continue
            s[i] = -s[i]
            Ms = Ms + (-2.0 * s[i]) * (-1) * M[:, i]  # s[i] already flipped; recompute cleanly
            Ms = M @ s
        v = weighted_cut(weighted, s) if weighted is not None else cut_value(adj, s)
        best = max(best, v)
    return best


def three_regular(N, rng):
    # configuration model for a 3-regular graph; retry on failure
    for _ in range(50):
        stubs = np.repeat(np.arange(N), 3); rng.shuffle(stubs)
        A = np.zeros((N, N))
        ok = True
        for a, b in zip(stubs[0::2], stubs[1::2]):
            if a == b or A[a, b]:
                ok = False; break
            A[a, b] = A[b, a] = 1
        if ok and A.sum(1).min() == 6:   # each node degree 3 (row sum counts both dirs -> 3)...
            return A
    # fallback: ring + chords
    A = np.zeros((N, N))
    for i in range(N):
        A[i, (i+1) % N] = A[(i+1) % N, i] = 1
        A[i, (i+2) % N] = A[(i+2) % N, i] = 1
    return A


def dense_er(N, rng, p=0.5):
    A = (rng.random((N, N)) < p).astype(float)
    A = np.triu(A, 1); A = A + A.T
    return A


def main():
    SEEDS = [0, 1, 2]
    NS = [50, 100, 200, 400]
    res = {"batch9_scaling": [], "batch10_quantization": {}}

    for N in NS:
        for fam, gen in [("sparse_3reg", three_regular), ("dense_p0.5", dense_er)]:
            ratios = []
            for sd in SEEDS:
                rng = np.random.default_rng(1000 + sd + N)
                A = gen(N, rng)
                oim = oim_best_cut(A, restarts=12, rng=rng)
                cls = classical_best_cut(A, restarts=30, rng=rng)
                ratios.append(oim / cls if cls > 0 else 0.0)
            res["batch9_scaling"].append(
                {"N": N, "family": fam, "approx_ratio_mean": round(float(np.mean(ratios)), 4),
                 "approx_ratio_min": round(float(np.min(ratios)), 4)})

    # Batch 10: weighted Max-Cut, coupling quantization at N=100
    N = 100
    def quantize(W, bits):
        if bits is None:
            return W
        if bits == 1:
            return np.sign(W)
        lev = 2**bits - 1
        m = np.abs(W).max() or 1.0
        return np.round(W / m * (lev//2)) / (lev//2) * m
    q_rows = []
    for sd in SEEDS:
        rng = np.random.default_rng(7000 + sd)
        A = dense_er(N, rng, p=0.3)
        W = A * rng.uniform(0.2, 1.0, (N, N)); W = np.triu(W, 1); W = W + W.T
        full = oim_best_cut((W != 0).astype(float)*0 + W, restarts=12, rng=rng, weighted=W)
        row = {"seed": sd, "full_cut": round(full, 2)}
        for bits, name in [(4, "4bit"), (2, "2bit"), (1, "1bit_sign")]:
            Wq = quantize(W, bits)
            cq = oim_best_cut(Wq, restarts=12, rng=rng, weighted=W)
            row[name + "_retain"] = round(cq / full if full > 0 else 0.0, 4)
        q_rows.append(row)
    def mean_ret(k):
        return round(float(np.mean([r[k] for r in q_rows])), 4)
    res["batch10_quantization"] = {"rows": q_rows,
        "retain_4bit": mean_ret("4bit_retain"), "retain_2bit": mean_ret("2bit_retain"),
        "retain_1bit_sign": mean_ret("1bit_sign_retain")}

    # resolve
    def ratio_at(N, fam):
        for r in res["batch9_scaling"]:
            if r["N"] == N and r["family"] == fam:
                return r["approx_ratio_mean"]
        return None
    res["resolved"] = {
        "sparse_holds_at_scale_y": int(ratio_at(400, "sparse_3reg") >= 0.97),
        "dense_degrades_at_scale_y": int(ratio_at(400, "dense_p0.5") < 0.95),
        "hybrid_digital_coupling_feasible_y": int(res["batch10_quantization"]["retain_4bit"] >= 0.97),
    }
    res["sparse_ratio_N400"] = ratio_at(400, "sparse_3reg")
    res["dense_ratio_N400"] = ratio_at(400, "dense_p0.5")
    OUT.write_text(json.dumps(res, indent=2) + "\n")
    print(json.dumps(res, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
