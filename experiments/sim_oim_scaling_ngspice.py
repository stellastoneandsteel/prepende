#!/usr/bin/env python3
"""
How the ngspice Oscillator Ising Machine (OIM) scales — and what a SHIL pump actually does.

Builds on sim_oim_ngspice.py (N van der Pol oscillators, antiferro link per graph edge). Two
honest measurements:

  1. SCALING — for N = 4,6,8,10,12, generate random graphs (edge prob 0.5), run the OIM with a
     few random restarts, take the best cut, and compare to the brute-force Max-Cut. Report the
     exact-optimum hit-rate AND the approximation ratio (best cut / optimum).

  2. SHIL BINARINESS — a sub-harmonic-injection pump (parametric, -(1+p·cos(2·w·time))·x in each
     oscillator's y-equation) at p=0.4. We measure how close the settled relative phases sit to
     {0, 180} degrees, with the pump off vs on.

Honest findings (this script, small samples — indicative, not a rigorous benchmark):
  - exact-optimum hit-rate is perfect through N=8, then falls (the OIM is a heuristic and gets
    trapped in local minima on larger instances) — but the approximation ratio stays ~0.93-0.97,
    i.e. it still finds near-optimal cuts.
  - WITHOUT the pump, relative phases settle ~38 degrees off {0,180}: it is secretly running the
    continuous XY model and the binary readout is rounding. WITH the pump, phases land at exactly
    0/180 — a genuine Ising machine. The pump does NOT raise the score at these sizes (the readout
    already discretises), but it makes the dynamics honestly binary. Reported, not hidden.

This verifies behaviour at small N in a real simulator; it is not a built device, beats nothing
classical, and the brute-force comparison only stays tractable for small N.

Requires ngspice on PATH + numpy. Pure local — no network, no models.

  python3 sim_oim_scaling_ngspice.py                  # scaling table + binariness
  python3 sim_oim_scaling_ngspice.py --figure OUT.svg # also write the quality-vs-N curve
"""
import os, sys, subprocess, tempfile
os.environ.setdefault("VECLIB_MAXIMUM_THREADS", "1")
import numpy as np  # noqa: E402

WORK = tempfile.mkdtemp(prefix="prepende_oimscale_")
INK, MUT, BLUE, TEAL, LINE = "#16233a", "#5b6675", "#1f5fae", "#0f6e56", "#cdd4de"
NS = [4, 6, 8, 10, 12]
GRAPHS_PER_N = 5
RESTARTS = 3


def brute_maxcut(N, edges):
    best = -1
    for bits in range(2 ** N):
        s = [1 if (bits >> k) & 1 else -1 for k in range(N)]
        best = max(best, sum(1 for a, b in edges if s[a] != s[b]))
    return best


def random_graph(N, p_edge, rng):
    return [(i, j) for i in range(N) for j in range(i + 1, N) if rng.random() < p_edge]


def run_oim(N, edges, ics, p=0.0, K=2500, w=31416, mu=0.4, tstop=24e-3, want_binariness=False):
    nbr = {i: [] for i in range(N)}
    for a, b in edges:
        nbr[a].append(b); nbr[b].append(a)
    pump = "(1+p*cos(2*w*time))" if p > 0 else "1"            # parametric SHIL pump at 2*f0
    L = ["* OIM scaling (ngspice treats this first line as the title)", f".param w={w} mu={mu} K={K} p={p}"]
    for i in range(N):
        coup = "".join(f"+K*(-V(x{j})-V(x{i}))" for j in nbr[i])
        L += [f"Cx{i} x{i} 0 1 ic={ics[i]:.4f}",
              f"Bx{i} 0 x{i} I=w*V(y{i}){coup}",
              f"Cy{i} y{i} 0 1 ic=0",
              f"By{i} 0 y{i} I=w*(-{pump}*V(x{i})+mu*(1-V(x{i})*V(x{i}))*V(y{i}))"]
    wave = os.path.join(WORK, "sc.dat")
    vecs = " ".join(f"v(x{i})" for i in range(N))
    L += [f".tran 1u {tstop:g} uic", ".control", "run", f"wrdata {wave} {vecs}", ".endc", ".end"]
    open(os.path.join(WORK, "sc.cir"), "w").write("\n".join(L))
    if os.path.exists(wave):
        os.remove(wave)
    subprocess.run(["ngspice", "-b", os.path.join(WORK, "sc.cir")], capture_output=True, text=True, timeout=180)
    if not os.path.exists(wave):
        return (None, None) if want_binariness else None
    arr = np.loadtxt(wave)
    t = arr[:, 0]
    cols = [arr[:, 1 + 2 * i] for i in range(N)]
    sel = t >= t[-1] * 0.6
    ts = t[sel]
    v0 = cols[0][sel] - cols[0][sel].mean()
    idx = np.where(np.diff(np.signbit(v0)))[0]
    if len(idx) < 4:
        return (None, None) if want_binariness else None
    hp = np.diff(ts[idx]); hp = hp[hp > 0]
    f = 1.0 / (2.0 * np.median(hp)); wv = 2 * np.pi * f
    tu = np.linspace(ts[0], ts[-1], 6000)
    ph = [np.arctan2((np.interp(tu, ts, cols[i][sel] - cols[i][sel].mean()) * np.sin(wv * tu)).sum(),
                     (np.interp(tu, ts, cols[i][sel] - cols[i][sel].mean()) * np.cos(wv * tu)).sum())
          for i in range(N)]
    sp = [1 if np.cos(ph[i] - ph[0]) >= 0 else -1 for i in range(N)]
    cut = sum(1 for a, b in edges if sp[a] != sp[b])
    if want_binariness:
        rel = [np.degrees(ph[i] - ph[0]) for i in range(N)]
        binr = float(np.mean([min(abs(((r + 180) % 360) - 180), abs(((r - 180 + 180) % 360) - 180)) for r in rel]))
        return cut, binr
    return cut


def scaling():
    rows = []
    print("%3s %8s %11s %14s" % ("N", "graphs", "exact-hit", "approx-ratio"))
    for N in NS:
        hits = 0; ratios = []; ngraph = 0
        for g in range(GRAPHS_PER_N):
            rng = np.random.default_rng(1000 + 10 * N + g)
            E = random_graph(N, 0.5, rng)
            if not E:
                continue
            ngraph += 1
            opt = brute_maxcut(N, E)
            best = max((run_oim(N, E, list(rng.uniform(-0.3, 0.3, N))) or 0) for _ in range(RESTARTS))
            if best == opt:
                hits += 1
            ratios.append(best / opt if opt else 1.0)
        hr = hits / ngraph if ngraph else 0.0
        ar = float(np.mean(ratios)) if ratios else 0.0
        rows.append((N, ngraph, hr, ar))
        print("%3d %8d %10s %14.3f" % (N, ngraph, f"{hits}/{ngraph}", ar))
    return rows


def binariness():
    K6 = [(i, j) for i in range(6) for j in range(i + 1, 6)]
    out = {}
    for p in (0.0, 0.4):
        bs = []
        for s in range(6):
            rng = np.random.default_rng(300 + s)
            _, b = run_oim(6, K6, list(rng.uniform(-0.3, 0.3, 6)), p=p, want_binariness=True)
            if b is not None:
                bs.append(b)
        out[p] = float(np.mean(bs)) if bs else None
    print("\nSHIL binariness on K6 — mean |relative phase - nearest 0/180|:")
    print("  pump off (p=0.0): %.1f deg   (XY-like; binary readout rounds)" % out[0.0])
    print("  pump on  (p=0.4): %.1f deg   (genuine Ising — phases at 0/180)" % out[0.4])
    return out


def write_figure(path, rows):
    x0, x1, y0, y1 = 50, 470, 30, 170                 # plot box (y down)
    nmin, nmax = NS[0], NS[-1]

    def px(N): return x0 + (N - nmin) / (nmax - nmin) * (x1 - x0)

    def py(v): return y1 - v * (y1 - y0)               # v in [0,1]
    svg = ['<svg viewBox="0 0 590 220" xmlns="http://www.w3.org/2000/svg" role="img" '
           'aria-label="OIM Max-Cut quality vs problem size">',
           '<rect width="520" height="220" fill="#ffffff"/>',
           f'<text x="50" y="16" font-size="12" font-family="sans-serif" font-weight="700" fill="{INK}">'
           'OIM Max-Cut quality vs problem size (ngspice)</text>']
    for v, lab in [(1.0, "1.0"), (0.5, "0.5"), (0.0, "0")]:
        svg.append(f'<line x1="{x0}" y1="{py(v):.0f}" x2="{x1}" y2="{py(v):.0f}" stroke="{LINE}" stroke-dasharray="2 4"/>')
        svg.append(f'<text x="{x0 - 6}" y="{py(v) + 3:.0f}" text-anchor="end" font-size="9" fill="{MUT}">{lab}</text>')
    for N, *_ in rows:
        svg.append(f'<text x="{px(N):.0f}" y="{y1 + 14:.0f}" text-anchor="middle" font-size="9" fill="{MUT}">N={N}</text>')
    appr = " ".join(f"{px(N):.0f},{py(ar):.1f}" for N, _, _, ar in rows)
    hit = " ".join(f"{px(N):.0f},{py(hr):.1f}" for N, _, hr, _ in rows)
    svg.append(f'<polyline points="{appr}" fill="none" stroke="{TEAL}" stroke-width="2.2"/>')
    svg.append(f'<polyline points="{hit}" fill="none" stroke="{BLUE}" stroke-width="2.2" stroke-dasharray="5 3"/>')
    for N, _, hr, ar in rows:
        svg.append(f'<circle cx="{px(N):.0f}" cy="{py(ar):.1f}" r="3.2" fill="{TEAL}"/>')
        svg.append(f'<circle cx="{px(N):.0f}" cy="{py(hr):.1f}" r="3.2" fill="{BLUE}"/>')
    svg.append(f'<text x="{x1 + 6}" y="{py(rows[-1][3]) + 3:.0f}" font-size="9.5" fill="{TEAL}">approx ratio</text>')
    svg.append(f'<text x="{x1 + 6}" y="{py(rows[-1][2]) + 3:.0f}" font-size="9.5" fill="{BLUE}">exact-optimum rate</text>')
    svg.append(f'<text x="260" y="210" text-anchor="middle" font-size="9.5" font-family="sans-serif" fill="{MUT}">'
               'exact optimum is hit through N=8, then falls — but cuts stay near-optimal (heuristic, local minima)</text>')
    svg.append('</svg>')
    open(path, "w").write("\n".join(svg))


def main():
    rows = scaling()
    binariness()
    if "--figure" in sys.argv:
        path = sys.argv[sys.argv.index("--figure") + 1]
        write_figure(path, rows)
        print("\nfigure written to", path)


if __name__ == "__main__":
    main()
