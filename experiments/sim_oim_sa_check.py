#!/usr/bin/env python3
"""Honesty check: is the OIM's apparent sparse-graph edge real, or an artifact of
a weak (greedy local-search) baseline? Re-run the decisive cases against a proper
simulated-annealing baseline. Reports OIM/SA and OIM/greedyLS ratios.
"""
import json, math
from pathlib import Path
import numpy as np
import importlib.util

H = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("o", H / "oim_scale_niche.py")
o = importlib.util.module_from_spec(spec); spec.loader.exec_module(o)


def sa_best_cut(M, restarts, rng, sweeps=160):
    N = len(M); best = -1.0
    for _ in range(restarts):
        s = rng.choice([-1.0, 1.0], N); h = M @ s
        for sw in range(sweeps):
            T = 1.5 * (0.02/1.5) ** (sw/max(1, sweeps-1))
            for i in range(N):
                d = s[i] * h[i]                       # cut gain if flip i
                if d > 0 or rng.random() < math.exp(max(-30.0, d / T)):
                    s[i] = -s[i]; h += M[:, i] * (2.0 * s[i])
        v = o.cut_value(M, s)
        best = max(best, v)
    return best


def main():
    res = []
    for N in (200, 400):
        for fam, gen in [("sparse_3reg", o.three_regular), ("dense_p0.5", o.dense_er)]:
            oim_sa, oim_ls = [], []
            for sd in (0, 1, 2):
                rng = np.random.default_rng(1000 + sd + N)   # same seeds as main run
                A = gen(N, rng)
                rng2 = np.random.default_rng(5000 + sd + N)
                oim = o.oim_best_cut(A, restarts=12, rng=rng2)
                ls = o.classical_best_cut(A, restarts=30, rng=np.random.default_rng(6000+sd+N))
                sa = sa_best_cut(A, restarts=12, rng=np.random.default_rng(8000+sd+N))
                oim_sa.append(oim/sa if sa > 0 else 0)
                oim_ls.append(oim/ls if ls > 0 else 0)
            res.append({"N": N, "family": fam,
                        "OIM_over_SA": round(float(np.mean(oim_sa)), 4),
                        "OIM_over_greedyLS": round(float(np.mean(oim_ls)), 4)})
            print(res[-1])
    verdict = {"rows": res,
        "oim_beats_sa_on_sparse_N400": next(r["OIM_over_SA"] for r in res if r["N"] == 400 and r["family"] == "sparse_3reg") > 1.0,
        "interpretation": "if OIM/SA <= ~1.0 the apparent sparse 'win' was a weak-baseline artifact; if > 1.0 it survives a real SA"}
    (H / "models" / "oim_sa_check.json").write_text(json.dumps(verdict, indent=2) + "\n")
    print(json.dumps(verdict, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
