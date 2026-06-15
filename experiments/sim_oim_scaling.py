#!/usr/bin/env python3
"""
Scaling + ablation study for Build A (Oscillator Ising Machine) on Max-Cut.

Extends sim_oim_benchmark.py with two studies the paper and the physical build need:

  (1) SIZE SCALING vs simulated annealing at matched restarts -- solution quality
      (OIM/best, SA/best, OIM hit-rate) and SOFTWARE effort (wall seconds each).

  (2) SHIL / NOISE ABLATION at fixed n -- how sensitive solution quality is to the
      sub-harmonic-injection-lock strength (Ks_max) and the injected noise (noise0).
      This directly bounds the PHYSICAL precision requirement: if quality collapses
      outside a narrow Ks/noise window, the bench's op-amps/passives must hit that
      window. That is the honest hardware-feasibility question, answered in software.

HONESTY CAVEAT (unchanged from the benchmark): this integrates the OIM ODE on a CPU,
so it measures SOLUTION QUALITY and SOFTWARE effort, NOT the analog energy/latency
advantage. In software, integrating the ODE is *slower* than SA -- expect SA wall-time
to win here. The energy-per-solution win is pre-registered hypothesis [T1], to be
measured on a bench. It is NOT claimed by this script.
"""
import os, sys, time, json, statistics as S, warnings
os.environ.setdefault("VECLIB_MAXIMUM_THREADS", "1")
warnings.filterwarnings("ignore", category=RuntimeWarning)
import numpy as np  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sim_oim_benchmark import random_graph, oim_solve, sa_solve  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def scaling(sizes, seeds, oim_restarts=30, sa_restarts=15, steps=1200, sweeps=120):
    print("(1) SIZE SCALING - OIM dynamics vs simulated annealing, random p=0.5, matched restarts")
    print("%4s%8s%8s%10s%9s%10s%9s%8s" %
          ("n", "edges", "ref", "OIM/best", "SA/best", "OIM hit%", "OIM s", "SA s"))
    rows = []
    for n in sizes:
        oimq, saq, hit, ot, st, refs, E = [], [], [], [], [], [], 0
        for seed in seeds:
            A = random_graph(n, 0.5, seed); E = int(A.sum() // 2)
            t0 = time.time(); ob, hits = oim_solve(A, restarts=oim_restarts, steps=steps, seed=seed)
            ot.append(time.time() - t0)
            t0 = time.time(); sb = sa_solve(A, restarts=sa_restarts, sweeps=sweeps, seed=seed)
            st.append(time.time() - t0)
            ref = max(ob, sb); refs.append(ref)
            oimq.append(ob / ref); saq.append(sb / ref)
            hit.append(100.0 * sum(1 for h in hits if h >= ref) / len(hits))
        row = {"n": n, "edges": E, "ref": int(S.mean(refs)),
               "oim_ratio": round(S.mean(oimq), 4), "sa_ratio": round(S.mean(saq), 4),
               "oim_hitpct": round(S.mean(hit), 1),
               "oim_sec": round(S.mean(ot), 3), "sa_sec": round(S.mean(st), 3)}
        rows.append(row)
        print("%4d%8d%8d%9.1f%%%8.1f%%%9.1f%%%8.2f%8.2f" %
              (n, E, row["ref"], row["oim_ratio"] * 100, row["sa_ratio"] * 100,
               row["oim_hitpct"], row["oim_sec"], row["sa_sec"]))
    return rows


def ablation(n=80, seeds=(1, 2), restarts=20, steps=1200):
    # Metric = MEAN per-restart cut / ref (NOT best-over-restarts, which saturates and
    # hides parameter sensitivity). This is the discriminating, honest measure of how
    # well a SINGLE physical solve does -- the thing the hardware actually buys you.
    print("\n(2) SHIL/NOISE ABLATION at n=%d - MEAN per-restart OIM cut as %% of ref" % n)
    print("    rows = Ks_max (SHIL lock strength), cols = noise0 (injected noise)")
    Ks_list = [0.6, 1.2, 1.8, 2.4]
    noise_list = [0.3, 0.7, 1.1]
    refs = {}
    for seed in seeds:  # strong reference per graph
        A = random_graph(n, 0.5, seed)
        ob, _ = oim_solve(A, restarts=40, steps=1500, seed=seed)
        sb = sa_solve(A, restarts=30, sweeps=160, seed=seed)
        refs[seed] = (A, max(ob, sb))
    print("%10s" % "Ks\\noise" + "".join("%9.1f" % x for x in noise_list))
    grid = []
    for Ks in Ks_list:
        cells = []
        for noise in noise_list:
            q = []
            for seed in seeds:
                A, ref = refs[seed]
                _, hits = oim_solve(A, restarts=restarts, steps=steps,
                                    Ks_max=Ks, noise0=noise, seed=seed)
                q.append(S.mean(hits) / ref)  # mean per-restart quality
            cells.append(round(S.mean(q) * 100, 1))
        grid.append({"Ks_max": Ks, "by_noise": dict(zip(noise_list, cells))})
        print("%10.1f" % Ks + "".join("%8.1f%%" % c for c in cells))
    return {"n": n, "metric": "mean_per_restart_cut_over_ref",
            "Ks_list": Ks_list, "noise_list": noise_list, "grid": grid}


if __name__ == "__main__":
    print("Build A scaling + ablation (software quality study; energy is a hardware claim)")
    print("=" * 78)
    sizes = [20, 40, 60, 80, 100, 120]
    seeds = (1, 2)
    sc = scaling(sizes, seeds)
    ab = ablation(n=80, seeds=seeds)
    print("=" * 78)
    print("ref = best cut found by either method (no brute-force at this scale).")
    print("Read the ablation as: where does OIM/best stay high? That window is the")
    print("coupling-lock + noise tolerance the physical bench must hold.")
    out = {"study": "oim-scaling+ablation", "p": 0.5, "seeds": list(seeds),
           "scaling": sc, "ablation": ab}
    with open(os.path.join(HERE, "data", "oim-scaling.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    print("wrote data/oim-scaling.json")
