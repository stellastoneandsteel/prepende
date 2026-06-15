#!/usr/bin/env python3
"""
A small Oscillator Ising Machine (OIM) in ngspice — scaling the coupled-oscillator cell from
two spins to N, and using it to solve small Max-Cut instances in an actual circuit simulator
(not just numpy).

Each spin is a self-sustaining van der Pol oscillator (phase 0/pi = +/-1). For a graph, every
edge gets an ANTIFERRO link (a "-1 inverter" coupling), so the network's low-energy state wants
the two ends of each edge in opposite phase — i.e. on opposite sides of the cut. ngspice
integrates the dynamics; we read each oscillator's settled phase relative to oscillator 0
(in-phase = +1, anti-phase = -1) and count the cut.

Result of the sweep below (8 random initial conditions per graph):
  4-cycle ............ opt 4, 8/8 optimal
  5-cycle (frustrated) opt 4, 8/8 optimal
  K4 ................. opt 4, 8/8 optimal
  K5 (frustrated) .... opt 6, 7/8 optimal  (one run finds 5)
  random 6-node ...... opt 8, 8/8 optimal
  => 39/40 runs hit the brute-force optimum.

Honest scope: this is a HEURISTIC Ising solver, not guaranteed-optimal — the single K5 miss is
the proof of that, shown not hidden. It has no sub-harmonic-injection (SHIL) drive yet, so on
larger / more frustrated graphs phases can settle between 0 and pi; adding SHIL (which forces
phases to exactly 0 or pi) is the next step for scaling. This verifies the *mechanism* at small N
in a real simulator; it is not a built device, and the brute-force comparison only stays tractable
for small N.

Requires ngspice on PATH (`brew install ngspice`) and numpy. Pure local — no network, no models.

  python3 sim_oim_ngspice.py                   # run the graph sweep, print the hit-rate table
  python3 sim_oim_ngspice.py --figure OUT.svg  # also write a bipartition figure (frustrated 5-cycle)
"""
import os, sys, subprocess, tempfile
os.environ.setdefault("VECLIB_MAXIMUM_THREADS", "1")
import numpy as np  # noqa: E402

WORK = tempfile.mkdtemp(prefix="prepende_oim_")
INK, MUT, BLUE, MAR, TEAL, LINE = "#16233a", "#5b6675", "#1f5fae", "#7a2638", "#0f6e56", "#cdd4de"


def brute_maxcut(N, edges):
    best, bests = -1, None
    for bits in range(2 ** N):
        s = [1 if (bits >> k) & 1 else -1 for k in range(N)]
        c = sum(1 for a, b in edges if s[a] != s[b])
        if c > best:
            best, bests = c, s[:]
    return best, bests


def run_oim(N, edges, ics, K=2500, w=31416, mu=0.4, tstop=24e-3):
    """Build + simulate the OIM netlist; return the settled spins (or None on a bad run)."""
    nbr = {i: [] for i in range(N)}
    for a, b in edges:
        nbr[a].append(b); nbr[b].append(a)
    L = ["* oscillator Ising machine (ngspice treats this first line as the title)",
         f".param w={w} mu={mu} K={K}"]
    for i in range(N):
        coup = "".join(f"+K*(-V(x{j})-V(x{i}))" for j in nbr[i])     # antiferro link per edge
        L += [f"Cx{i} x{i} 0 1 ic={ics[i]:.4f}",
              f"Bx{i} 0 x{i} I=w*V(y{i}){coup}",
              f"Cy{i} y{i} 0 1 ic=0",
              f"By{i} 0 y{i} I=w*(-V(x{i})+mu*(1-V(x{i})*V(x{i}))*V(y{i}))"]
    wave = os.path.join(WORK, "oim.dat")
    vecs = " ".join(f"v(x{i})" for i in range(N))
    L += [f".tran 1u {tstop:g} uic", ".control", "run", f"wrdata {wave} {vecs}", ".endc", ".end"]
    open(os.path.join(WORK, "oim.cir"), "w").write("\n".join(L))
    if os.path.exists(wave):
        os.remove(wave)
    subprocess.run(["ngspice", "-b", os.path.join(WORK, "oim.cir")], capture_output=True, text=True, timeout=120)
    if not os.path.exists(wave):
        return None
    arr = np.loadtxt(wave)
    t = arr[:, 0]
    cols = [arr[:, 1 + 2 * i] for i in range(N)]      # wrdata interleaves a time column per vector
    sel = t >= t[-1] * 0.6                             # steady window (after the network settles)
    ts = t[sel]
    v0 = cols[0][sel] - cols[0][sel].mean()
    idx = np.where(np.diff(np.signbit(v0)))[0]
    if len(idx) < 4:
        return None
    hp = np.diff(ts[idx]); hp = hp[hp > 0]
    f = 1.0 / (2.0 * np.median(hp)); wv = 2 * np.pi * f
    tu = np.linspace(ts[0], ts[-1], 6000)

    def phase(c):
        a = np.interp(tu, ts, c[sel] - c[sel].mean())
        return np.arctan2((a * np.sin(wv * tu)).sum(), (a * np.cos(wv * tu)).sum())
    ph = [phase(cols[i]) for i in range(N)]
    return [1 if np.cos(ph[i] - ph[0]) >= 0 else -1 for i in range(N)]   # spin relative to osc 0


def cut_value(edges, spins):
    return sum(1 for a, b in edges if spins[a] != spins[b])


def complete(n):
    return [(i, j) for i in range(n) for j in range(i + 1, n)]


GRAPHS = {
    "4-cycle":              (4, [(0, 1), (1, 2), (2, 3), (3, 0)]),
    "5-cycle (frustrated)": (5, [(0, 1), (1, 2), (2, 3), (3, 4), (4, 0)]),
    "K4":                   (4, complete(4)),
    "K5 (frustrated)":      (5, complete(5)),
    "random 6-node":        (6, [(0, 1), (0, 3), (1, 2), (1, 4), (2, 5), (3, 4), (4, 5), (2, 3)]),
}


def sweep(seeds=8):
    print("%-22s %4s %5s %9s %6s" % ("graph", "opt", "best", "hit-rate", "mean"))
    hit_total = run_total = 0
    for name, (N, E) in GRAPHS.items():
        opt, _ = brute_maxcut(N, E)
        cuts = []
        for s in range(seeds):
            rng = np.random.default_rng(100 + s)
            sp = run_oim(N, E, list(rng.uniform(-0.3, 0.3, N)))
            if sp:
                cuts.append(cut_value(E, sp))
        hits = sum(1 for c in cuts if c == opt)
        hit_total += hits; run_total += len(cuts)
        print("%-22s %4d %5d %8s  %5.2f" % (name, opt, max(cuts), f"{hits}/{len(cuts)}", np.mean(cuts)))
    print("-" * 52)
    print("TOTAL: %d/%d runs hit the brute-force optimum" % (hit_total, run_total))


def write_figure(path, name="5-cycle (frustrated)"):
    """Draw one real OIM solution as a graph bipartition (nodes coloured by spin, cut edges bold)."""
    N, E = GRAPHS[name]
    opt, _ = brute_maxcut(N, E)
    sp = None
    for s in range(12):                                # pick a run that reached the optimum
        rng = np.random.default_rng(100 + s)
        cand = run_oim(N, E, list(rng.uniform(-0.3, 0.3, N)))
        if cand and cut_value(E, cand) == opt:
            sp = cand; break
    sp = sp or cand
    cut = cut_value(E, sp)
    cx, cy, r = 150, 150, 95
    pos = [(cx + r * np.cos(2 * np.pi * k / N - np.pi / 2),
            cy + r * np.sin(2 * np.pi * k / N - np.pi / 2)) for k in range(N)]
    svg = [f'<svg viewBox="0 0 300 332" xmlns="http://www.w3.org/2000/svg" role="img" '
           f'aria-label="OIM Max-Cut solution for the {name}">',
           '<rect width="300" height="320" fill="#ffffff"/>',
           f'<text x="150" y="20" text-anchor="middle" font-size="12" font-family="sans-serif" '
           f'font-weight="700" fill="{INK}">{name} — OIM solution</text>']
    for a, b in E:
        is_cut = sp[a] != sp[b]
        col = TEAL if is_cut else LINE
        dash = '' if is_cut else ' stroke-dasharray="3 4"'
        svg.append(f'<line x1="{pos[a][0]:.1f}" y1="{pos[a][1]:.1f}" x2="{pos[b][0]:.1f}" '
                   f'y2="{pos[b][1]:.1f}" stroke="{col}" stroke-width="{2.4 if is_cut else 1.3}"{dash}/>')
    for k in range(N):
        col = BLUE if sp[k] == 1 else MAR
        svg.append(f'<circle cx="{pos[k][0]:.1f}" cy="{pos[k][1]:.1f}" r="13" fill="{col}"/>'
                   f'<text x="{pos[k][0]:.1f}" y="{pos[k][1] + 4:.1f}" text-anchor="middle" '
                   f'font-size="11" font-family="sans-serif" font-weight="700" fill="#fff">{k}</text>')
    svg.append(f'<text x="150" y="296" text-anchor="middle" font-size="11" font-family="sans-serif" '
               f'fill="{TEAL}">cut = {cut} · brute-force optimum = {opt} · teal edges cut</text>')
    svg.append(f'<text x="150" y="314" text-anchor="middle" font-size="10.5" font-family="sans-serif" '
               f'fill="{MUT}"><tspan fill="{BLUE}">blue</tspan> / <tspan fill="{MAR}">maroon</tspan> = spin ±1</text>')
    svg.append('</svg>')
    open(path, "w").write("\n".join(svg))
    return cut, opt


def main():
    sweep()
    if "--figure" in sys.argv:
        path = sys.argv[sys.argv.index("--figure") + 1]
        cut, opt = write_figure(path)
        print("figure written to %s (cut %d / opt %d)" % (path, cut, opt))


if __name__ == "__main__":
    main()
