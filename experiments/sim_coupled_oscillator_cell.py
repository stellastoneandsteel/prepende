#!/usr/bin/env python3
"""
The coupled-oscillator Ising cell, in ngspice — the simulation behind the site's
"The build — simulated, and buildable" section. Re-runs it and prints every number shown
there, so you can verify them yourself.

Two stages, both checked by ngspice (the simulator is the oracle: a netlist that will not
oscillate is caught here, not trusted):

  Stage 1 — a single LC tank tuned to 5 kHz (L = 1 mH, C = 1.013 uF). The resonant frequency
            is f = 1 / (2*pi*sqrt(L*C)); we pick C in closed form for the target, then let
            ngspice ring the tank and read the frequency off the zero-crossings.
            Expected: ~5000 Hz (0% target error).

  Stage 2 — two self-sustaining (van der Pol / limit-cycle) oscillators, coupled. The SIGN
            of the coupling sets the locked phase: a direct resistor -> ferro -> they lock
            IN-PHASE (~0 deg, same Ising spin); a -1 inverter -> antiferro -> they lock
            ANTI-PHASE (~180 deg, opposite spins). That is the whole claim made physical:
            the computation lives in the coupling, not in the nodes.

Honest scope: this verifies the *mechanism* — one tuned oscillator, and a two-spin cell. It is
NOT a full Max-Cut solver and NOT a built device. The van der Pol oscillator is the op-amp /
analog-computer realisation (two integrators + a cubic nonlinearity), which is buildable; the
behavioural B-source form below is its exact ODE. The site's hero photograph is a concept
render, not output of this script (the schematic is a hand-drawn illustration in the page).

Requires: ngspice on PATH (`brew install ngspice`) and numpy. Pure local — no network, no models.

  python3 sim_coupled_oscillator_cell.py                  # print the measured numbers
  python3 sim_coupled_oscillator_cell.py --figures FIGDIR # also (re)write the two waveform SVGs
"""
import os, sys, subprocess, tempfile
os.environ.setdefault("VECLIB_MAXIMUM_THREADS", "1")
import numpy as np  # noqa: E402

WORK = tempfile.mkdtemp(prefix="prepende_osc_")


def ngspice(netlist, wave):
    cir = os.path.join(WORK, "c.cir")
    open(cir, "w").write(netlist)
    if os.path.exists(wave):
        os.remove(wave)
    subprocess.run(["ngspice", "-b", cir], capture_output=True, text=True, timeout=120)
    if not os.path.exists(wave):
        raise RuntimeError("ngspice produced no output — is ngspice installed, and is the netlist valid?")
    return np.loadtxt(wave)


def freq_from_zero_crossings(t, v):
    """Robust on ngspice's non-uniform .tran samples (an FFT on raw samples would be wrong).
    Each crossing time is linearly interpolated between the two straddling samples, so the
    frequency is accurate to well below one timestep (no quantisation bias)."""
    v = v - v.mean()
    idx = np.where(np.diff(np.signbit(v)))[0]
    if len(idx) < 4:
        return None
    cross = []
    for i in idx:
        v0, v1 = v[i], v[i + 1]
        if v1 != v0:
            cross.append(t[i] - v0 * (t[i + 1] - t[i]) / (v1 - v0))   # sub-sample interpolation
    cross = np.asarray(cross)
    half = np.diff(cross)
    half = half[half > 0]
    if len(half) < 2:
        return None
    return 1.0 / (2.0 * float(np.median(half)))


def phase_diff_deg(t, a, b, f):
    """Phase difference of two traces at frequency f, via sin/cos projection on a uniform grid."""
    tu = np.linspace(t[0], t[-1], 6000)
    w = 2 * np.pi * f
    A = np.interp(tu, t, a - a.mean())
    B = np.interp(tu, t, b - b.mean())
    p1 = np.arctan2((A * np.sin(w * tu)).sum(), (A * np.cos(w * tu)).sum())
    p2 = np.arctan2((B * np.sin(w * tu)).sum(), (B * np.cos(w * tu)).sum())
    return float(np.degrees((p1 - p2 + np.pi) % (2 * np.pi) - np.pi))


# ----- Stage 1: single LC tank at a target frequency -----
def lc_tank(ftarget=5000.0, L=1e-3):
    C = 1.0 / ((2 * np.pi * ftarget) ** 2 * L)      # closed-form C for the target
    T = 1.0 / ftarget
    wave = os.path.join(WORK, "s1.dat")
    net = f"""* Stage 1: LC tank tuned to {ftarget:.0f} Hz (L={L:.4g} H, C={C:.4g} F)
L1 out 0 {L:.6g} ic=0
C1 out 0 {C:.6g} ic=1
R1 out 0 1meg
.tran {T / 2000:.4g} {40 * T:.4g} uic
.control
run
wrdata {wave} v(out)
.endc
.end
"""
    arr = ngspice(net, wave)
    t, v = arr[:, 0], arr[:, 1]
    f = freq_from_zero_crossings(t[t >= 3 * T], v[t >= 3 * T])   # skip the startup cycle
    return {"L": L, "C": C, "target": ftarget, "measured": f,
            "err": (abs(f - ftarget) / ftarget if f else None), "t": t, "v": v}


# ----- Stage 2: two coupled van der Pol oscillators -----
def coupled_cell(coupling):
    # van der Pol in Lienard form: x' = w*y ; y' = w*(-x + mu*(1-x^2)*y). Coupling on the x-equation.
    if coupling == "ferro":          # direct resistor -> diffusive -> in-phase
        c1, c2 = "+K*(V(x2)-V(x1))", "+K*(V(x1)-V(x2))"
    else:                            # via a -1 inverter -> antiferro -> anti-phase
        c1, c2 = "+K*(-V(x2)-V(x1))", "+K*(-V(x1)-V(x2))"
    wave = os.path.join(WORK, f"{coupling}.dat")
    net = f"""* Stage 2: two coupled van der Pol oscillators ({coupling})
.param w=31416 mu=0.4 K=4000
Cx1 x1 0 1 ic=0.3
Bx1 0 x1 I=w*V(y1){c1}
Cy1 y1 0 1 ic=0
By1 0 y1 I=w*(-V(x1)+mu*(1-V(x1)*V(x1))*V(y1))
Cx2 x2 0 1 ic=-0.15
Bx2 0 x2 I=w*V(y2){c2}
Cy2 y2 0 1 ic=0
By2 0 y2 I=w*(-V(x2)+mu*(1-V(x2)*V(x2))*V(y2))
.tran 1u 16m uic
.control
run
wrdata {wave} v(x1) v(x2)
.endc
.end
"""
    arr = ngspice(net, wave)
    t = arr[:, 0]
    x1 = arr[:, 1]
    x2 = arr[:, 3] if arr.shape[1] >= 4 else arr[:, 2]
    sel = t >= 11e-3                  # measure after the lock has settled
    f = freq_from_zero_crossings(t[sel], x1[sel])
    d = phase_diff_deg(t[sel], x1[sel], x2[sel], f) if f else None
    return {"coupling": coupling, "freq": f, "phase_deg": d, "t": t, "x1": x1, "x2": x2}


# ----- optional: regenerate the two waveform SVGs shown on the site -----
def _poly(xs, ys, x0, x1, y1, ymin, ymax):
    sx = (x1 - x0) / (xs[-1] - xs[0])
    sy = (y1 - 20) / (ymax - ymin)
    return " ".join(f"{x0 + (x - xs[0]) * sx:.1f},{y1 - (y - ymin) * sy:.1f}" for x, y in zip(xs, ys))


def _window(t, cols, t0, t1):
    sel = (t >= t0) & (t <= t1)
    tu = np.linspace(t0, t1, 400)
    return tu, [np.interp(tu, t[sel], c[sel]) for c in cols]


def write_figures(s1, fer, ant, outdir):
    os.makedirs(outdir, exist_ok=True)
    T = 1.0 / s1["target"]
    tu, (v,) = _window(s1["t"], [s1["v"]], 0, 8 * T)
    err = "%.1f%%" % (s1["err"] * 100)
    svg1 = (f'<svg viewBox="0 0 560 170" xmlns="http://www.w3.org/2000/svg" role="img" '
            f'aria-label="ngspice waveform: LC tank at {s1["target"]:.0f} Hz">'
            '<rect x="0" y="0" width="560" height="170" fill="#ffffff"/>'
            '<line x1="40" y1="85" x2="540" y2="85" stroke="#e3e0d8"/>'
            f'<polyline points="{_poly(tu, v, 40, 540, 150, -1.05, 1.05)}" fill="none" stroke="#1f5fae" stroke-width="1.8"/>'
            '<text x="40" y="14" font-size="11" font-family="sans-serif" fill="#16233a">'
            '<tspan font-weight="700">Stage 1 · single LC oscillator</tspan>  —  ngspice transient, v(out)</text>'
            f'<text x="540" y="165" text-anchor="end" font-size="10" font-family="sans-serif" fill="#0f6e56">'
            f'target {s1["target"]:.0f} Hz · measured {s1["measured"]:.0f} Hz · {err} error</text></svg>')
    open(os.path.join(outdir, "fig_stage1.svg"), "w").write(svg1)

    def panel(r, label, sub, note, xoff):
        tu, (a, b) = _window(r["t"], [r["x1"], r["x2"]], 11e-3, 13e-3)
        return (f'<rect x="{xoff}" y="24" width="250" height="120" fill="#ffffff" stroke="#e3e0d8"/>'
                f'<polyline points="{_poly(tu, a, xoff + 8, xoff + 242, 136, -2.3, 2.3)}" fill="none" stroke="#1f5fae" stroke-width="1.7"/>'
                f'<polyline points="{_poly(tu, b, xoff + 8, xoff + 242, 136, -2.3, 2.3)}" fill="none" stroke="#7a2638" stroke-width="1.7" stroke-dasharray="4 3"/>'
                f'<text x="{xoff + 6}" y="20" font-size="11" font-family="sans-serif" fill="#16233a"><tspan font-weight="700">{label}</tspan> {sub}</text>'
                f'<text x="{xoff + 242}" y="158" text-anchor="end" font-size="10" font-family="sans-serif" fill="#5b6675">{note}</text>')
    svg2 = (f'<svg viewBox="0 0 560 175" xmlns="http://www.w3.org/2000/svg" role="img" '
            'aria-label="coupled oscillators: in-phase vs anti-phase">'
            + panel(fer, "Ferro coupling", f"→ {abs(fer['phase_deg']):.0f}° in-phase", "same spin  ↑↑", 8)
            + panel(ant, "Antiferro (−1 inverter)", f"→ {abs(ant['phase_deg']):.0f}° anti-phase", "opposite spins  ↑↓", 302)
            + '<text x="8" y="172" font-size="9" font-family="sans-serif" fill="#5b6675">'
            'blue = oscillator 1, maroon = oscillator 2 · the coupling sign sets the spins (compute in the coupling)</text></svg>')
    open(os.path.join(outdir, "fig_stage2.svg"), "w").write(svg2)
    return outdir


def main():
    s1 = lc_tank()
    fer = coupled_cell("ferro")
    ant = coupled_cell("antiferro")

    print("=" * 70)
    print("Coupled-oscillator Ising cell  ·  ngspice verification")
    print("=" * 70)
    print("Stage 1 — LC tank tuned to %.0f Hz (L=%.4g H, C=%.4g F)"
          % (s1["target"], s1["L"], s1["C"]))
    print("  measured: %.1f Hz   target error: %.2f%%" % (s1["measured"], s1["err"] * 100))
    print("Stage 2 — two coupled van der Pol oscillators (~%.0f Hz)" % (fer["freq"] or 0))
    print("  ferro     (direct resistor): phase diff %+.1f deg   -> %s"
          % (fer["phase_deg"], "IN-PHASE  (same spin)" if abs(fer["phase_deg"]) < 30 else "?"))
    print("  antiferro (−1 inverter)   : phase diff %+.1f deg   -> %s"
          % (ant["phase_deg"], "ANTI-PHASE (opposite spin)" if abs(abs(ant["phase_deg"]) - 180) < 30 else "?"))
    print("=" * 70)

    if "--figures" in sys.argv:
        outdir = sys.argv[sys.argv.index("--figures") + 1]
        write_figures(s1, fer, ant, outdir)
        print("waveform SVGs written to", outdir)


if __name__ == "__main__":
    main()
