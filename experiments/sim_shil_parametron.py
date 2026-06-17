#!/usr/bin/env python3
"""sim_shil_parametron.py — the single-cell parametron: a measured Ising bit.

A degenerate parametric oscillator is the cleanest possible Ising spin. An LC
tank resonant at f0 is given a negative-resistance element (a bounded tanh
transconductor, i.e. a cross-coupled diff-pair) whose DC gain is held BELOW the
self-oscillation threshold. Energy is supplied only by modulating that
conductance at the pump frequency 2*f0 (a degenerate parametric pump). The cell
then oscillates at f0 = pump/2 (sub-harmonic injection locking) and locks into
one of two phases, 0 or pi — that phase is the spin (+/-1).

This script reproduces two results plotted on the site, using ngspice as the
oracle (nothing hand-drawn):

  (A) Bistability: with the pump on, the cell oscillates at exactly pump/2; from
      opposite seeds it settles 180 deg apart; intermediate seeds binarise onto
      one state. With the pump off, it decays to 0 V (oscillation is pump-born).

  (B) Sub-threshold tunable memory: held below threshold the cell does not
      oscillate, but its envelope decay time constant tau grows as the pump
      approaches threshold (critical slowing down) — a one-knob memory depth.

Requires: ngspice (tested on v46) on PATH, numpy. Run: python3 sim_shil_parametron.py
"""
import json, math, subprocess, sys, tempfile
from pathlib import Path

import numpy as np

F0 = 1000.0                         # tank resonance, Hz
W0 = 2 * math.pi * F0
WP = 2 * math.pi * (2 * F0)         # pump = 2*f0
C = 1e-6                            # F
L = 1.0 / (W0**2 * C)              # -> 25.33 mH for exactly f0 = 1.000 kHz
RESR = 1.0                          # inductor ESR (ohm); tank loss G ~ RESR/XL^2
G_LOSS = RESR / (W0 * L)**2
GP_TH = 2 * G_LOSS                 # describing-function parametric threshold (~79 uS)

NETLIST = """* SHIL parametron cell
L1  osc nL {L:.6e} ic={IL:.6e}
RL  nL  0  {RESR}
C1  osc 0  {C:.6e}
Bnl osc 0  I = -({GM0:g} + {GP:g}*cos({WP:.9f}*time)) * 0.5 * tanh(V(osc)/0.5)
.ic v(osc)={VC:.6e}
.options reltol=1e-4 abstol=1e-12 vntol=1e-9
.control
tran {STEP} {STOP} 0 {MAXSTEP} uic
linearize v(osc)
wrdata {OUT} v(osc)
.endc
.end
"""


def _ngspice(vc, il, gp, gm0=0.0, stop=0.5, step="2u", maxstep="5u"):
    d = Path(tempfile.mkdtemp())
    out = d / "o.dat"
    cir = d / "c.cir"
    cir.write_text(NETLIST.format(L=L, IL=il, RESR=RESR, C=C, GM0=gm0, GP=gp,
                                  WP=WP, VC=vc, STEP=step, STOP=stop,
                                  MAXSTEP=maxstep, OUT=str(out)))
    subprocess.run(["ngspice", "-b", str(cir)], capture_output=True, text=True)
    t, v = [], []
    if out.exists():
        for ln in out.read_text().splitlines():
            p = ln.split()
            if len(p) >= 2:
                try:
                    t.append(float(p[0])); v.append(float(p[1]))
                except ValueError:
                    pass
    return np.array(t), np.array(v)


def _phase_freq_amp(t, v):
    tail = t > t[-1] * 0.7
    ts, vs = t[tail], v[tail]
    amp = float((vs.max() - vs.min()) / 2)
    dt = float(np.median(np.diff(ts)))
    V = vs - vs.mean()
    F = np.abs(np.fft.rfft(V * np.hanning(len(V))))
    f_dom = float(np.fft.rfftfreq(len(V), dt)[np.argmax(F)])
    I = float(np.mean(vs * np.cos(W0 * ts)))
    Q = float(np.mean(vs * np.sin(W0 * ts)))
    return round(f_dom, 1), round(amp, 3), round(math.degrees(math.atan2(-Q, I)) % 360, 1)


def _tau(t, v):
    per = 1.0 / F0
    centers, env, k = [], [], 0
    while t[0] + (k + 1) * per <= t[-1]:
        m = (t >= t[0] + k * per) & (t < t[0] + (k + 1) * per)
        if m.sum() > 3:
            centers.append(t[0] + (k + 0.5) * per); env.append(float(np.max(np.abs(v[m]))))
        k += 1
    centers, env = np.array(centers), np.array(env)
    m = (centers > centers[0] + 3 * per) & (env > 1e-6)
    if m.sum() < 5:
        return None
    slope = np.polyfit(centers[m], np.log(env[m]), 1)[0]
    return round((-1.0 / slope) * 1e3, 1) if slope < 0 else float("inf")


def main():
    GP = 400e-6   # ~5x threshold -> robust parametric oscillation (Ising mode)
    print(f"f0 = {F0} Hz   pump = {2*F0} Hz   L = {L*1e3:.2f} mH   "
          f"G_loss = {G_LOSS*1e6:.1f} uS   GP_threshold = {GP_TH*1e6:.1f} uS\n")

    print("(A) bistability — pump on, opposite + quadrature seeds, and no-pump control:")
    for label, vc, il, gp in [("spin_up", +0.05, 0.0, GP), ("spin_down", -0.05, 0.0, GP),
                              ("quad", +0.035, 2.2e-4, GP), ("no_pump", +0.05, 0.0, 0.0)]:
        f, amp, ph = _phase_freq_amp(*_ngspice(vc, il, gp))
        print(f"  {label:9s}  f={f:7.1f} Hz  amp={amp:6.3f} V  phase={ph:6.1f} deg")

    print("\n(B) sub-threshold tunable memory — envelope tau vs pump amplitude:")
    print(f"  theory (no pump) tau = 2L/RL = {2*L/RESR*1e3:.2f} ms   Q = {W0*L/RESR:.1f}")
    for gp in (0.0, 30e-6, 50e-6, 65e-6, 75e-6):
        t, v = _ngspice(0.30, 0.0, gp, stop=0.4)
        tau = _tau(t, v)
        print(f"  GP={gp*1e6:5.1f} uS  ({gp/GP_TH:4.2f}x threshold)  tau={tau} ms")
    return 0


if __name__ == "__main__":
    sys.exit(main())
