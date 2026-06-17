#!/usr/bin/env python3
"""ngspice_reservoir.py — a REAL circuit-level coupled parametron reservoir.

Not the envelope abstraction: N actual sub-threshold SHIL parametron cells (LC
tank + tanh transconductor + 2f pump), resistively coupled, driven by an input
stream, simulated in ngspice. Per-symbol lock-in extracts each cell's (X,Y)
quadratures -> reservoir state; a ridge readout is trained on a task and
compared to a trained linear delay-line baseline. Validates whether the
envelope-model verdict (valid but weak) holds at the circuit level.

Task: y(t) = 0.5*u(t-1) + 0.5*u(t-1)*u(t-2)  (a linear term + a product the
linear baseline cannot represent). If the reservoir's nonlinearity is real it
beats the linear filter; if sub-threshold ~ linear, it ties the baseline's miss.
"""
import json, math, subprocess, sys, tempfile
from pathlib import Path
import numpy as np

HERE = Path(__file__).resolve().parent
RES = HERE / "results"; RES.mkdir(exist_ok=True)
F0 = 10_000.0
W0 = 2*math.pi*F0
WP = 2*math.pi*2*F0
C = 100e-9
L = 1.0/(W0**2*C)            # ~2.533 mH
GP_TH = 2 * (1.0/(W0*L)**2)  # parametric threshold conductance


def build_netlist(u, J, b, pfrac, cpl, isc, Tsym, periods_settle, out):
    N = len(b); gp = pfrac*GP_TH
    Gloss = 1.0/(W0*L)**2           # ~40 uS — input & coupling are CURRENTS at this scale
    # PWL input: piecewise-constant per symbol
    pts = []
    for k, uk in enumerate(u):
        t0 = k*Tsym
        pts.append(f"{t0:.9g} {uk:.6g}")
        pts.append(f"{t0+Tsym*0.999:.9g} {uk:.6g}")
    pwl = " ".join(pts)
    stop = len(u)*Tsym
    L_ = f"{L:.6e}"
    lines = [f"* ngspice {N}-cell sub-threshold parametron reservoir",
             f"Vu uin 0 PWL({pwl})",
             ".options reltol=2e-3 abstol=1e-10 vntol=1e-7 maxstep=2u"]
    nodes = []
    for i in range(N):
        o = f"osc{i}"; nL = f"nL{i}"; nodes.append(f"v({o})")
        lines += [f"L{i} {o} {nL} {L_}", f"RL{i} {nL} 0 1", f"C{i} {o} 0 {C:.6e}",
                  f"Bnl{i} {o} 0 I = -({gp:g}*cos({WP:.9f}*time))*0.5*tanh(V({o})/0.5)",
                  f"Binp{i} {o} 0 I = -{isc*Gloss*b[i]:.6e}*V(uin)*cos({W0:.9f}*time)"]
        cterms = "+".join(f"({J[i,j]:.5g})*V(osc{j})" for j in range(N) if j != i)
        if cterms:
            lines.append(f"Bcpl{i} {o} 0 I = -{cpl*Gloss:.6e}*({cterms})")
    nodestr = " ".join(nodes)
    lines += [".control", f"tran 0.05u {stop:.9g} 0 2u uic",
              f"linearize {nodestr}", f"wrdata {out} {nodestr}", ".endc", ".end"]
    return "\n".join(lines), N


def run(u, J, b, pfrac=0.85, cpl=0.0, isc=4.0, Tsym=None, settle=0.5):
    if Tsym is None:
        Tsym = 6.0/F0           # 6 carrier periods per symbol
    out = RES / "rsv.dat"
    cir = Path(tempfile.mkdtemp())/"r.cir"
    netlist, N = build_netlist(u, J, b, pfrac, cpl, isc, Tsym, settle, str(out))
    cir.write_text(netlist)
    r = subprocess.run(["ngspice", "-b", str(cir)], capture_output=True, text=True)
    if not out.exists():
        return None, (r.stdout+r.stderr)[-400:]
    raw = np.array([[float(x) for x in ln.split()] for ln in out.read_text().splitlines()
                    if ln.strip() and not ln.lstrip().startswith('*')])
    out.unlink()
    t = raw[:, 0]
    V = raw[:, 1::2][:, :N]      # wrdata pairs (t,v) per vector -> values at odd cols
    # per-symbol lock-in over the last (1-settle) of each window
    nsym = len(u); S = np.zeros((nsym, 2*N))
    cos = np.cos(W0*t); sin = np.sin(W0*t)
    for k in range(nsym):
        lo, hi = (k+settle)*Tsym, (k+1)*Tsym
        m = (t >= lo) & (t < hi)
        if m.sum() < 2:
            continue
        S[k, :N] = (V[m]*cos[m, None]).mean(0)
        S[k, N:] = (V[m]*sin[m, None]).mean(0)
    S = np.nan_to_num(S, nan=0.0, posinf=0.0, neginf=0.0)
    S = np.clip(S, -10, 10)
    return S, None


def product_target(u):
    y = np.zeros(len(u))
    for t in range(2, len(u)):
        y[t] = 0.5*u[t-1] + 0.5*u[t-1]*u[t-2]
    return y


def xor_target(u):                       # purely nonlinear: a linear filter cannot do it
    b = (u > 0.25).astype(float)
    y = np.zeros(len(u))
    for t in range(4, len(u)):
        y[t] = float(int(b[t-2]) ^ int(b[t-4]))
    return y


def ridge(S, Y, reg=1e-2):
    A = np.hstack([S, np.ones((len(S), 1))])
    return np.linalg.solve(A.T@A + reg*np.eye(A.shape[1]), A.T@Y)


def _split(S, wash, tr):
    blk = S[wash:wash+tr]
    mu = blk.mean(0); sd = blk.std(0)
    sd = np.where(sd < 1e-6, 1.0, sd)        # don't amplify dead columns -> no overflow
    z = lambda X: np.clip((X-mu)/sd, -50, 50)
    return z(S[wash:wash+tr]), z(S[wash+tr:])


def eval_nrmse(S, y, wash, tr):
    Str, Ste = _split(S, wash, tr)
    W = ridge(Str, y[wash:wash+tr])
    p = np.hstack([Ste, np.ones((len(Ste), 1))])@W
    yt = y[wash+tr:]
    return float(np.sqrt(np.mean((p-yt)**2))/(np.std(yt)+1e-12))


def eval_acc(S, y, wash, tr):            # binary accuracy at 0.5 threshold (XOR)
    Str, Ste = _split(S, wash, tr)
    W = ridge(Str, y[wash:wash+tr])
    p = np.hstack([Ste, np.ones((len(Ste), 1))])@W
    return float(((p > 0.5).astype(float) == y[wash+tr:]).mean())


def lin_states(u, lags=8):
    T = len(u); S = np.zeros((T, lags))
    for k in range(lags):
        S[k:, k] = u[:T-k]
    return S


def main():
    import sys
    N = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    NSYM = int(sys.argv[2]) if len(sys.argv) > 2 else 300
    wash, tr = 50, int((NSYM-50)*0.66)
    rng = np.random.default_rng(7)
    u = rng.uniform(0, 0.5, NSYM)
    yp, yx = product_target(u), xor_target(u)
    J = rng.standard_normal((N, N)); np.fill_diagonal(J, 0)
    J /= max(abs(np.linalg.eigvals(J)))
    b = rng.standard_normal(N)

    LS = lin_states(u)
    out = {"N": N, "nsym": NSYM, "f0": F0,
           "linear_product_nrmse": round(eval_nrmse(LS, yp, wash, tr), 4),
           "linear_xor_acc": round(eval_acc(LS, yx, wash, tr), 4),
           "runs": []}
    best_p = (1e9, None); best_x = (-1, None)
    for pfrac in (0.4, 0.7, 0.9):
        for cpl in (0.0, 0.4):
            S, err = run(u, J, b, pfrac=pfrac, cpl=cpl, isc=3.0)
            if S is None:
                out["runs"].append({"pfrac": pfrac, "cpl": cpl, "error": err}); continue
            amp = float(np.sqrt(S[:, :N]**2 + S[:, N:]**2).mean())
            pr = eval_nrmse(S, yp, wash, tr); xa = eval_acc(S, yx, wash, tr)
            out["runs"].append({"pfrac": pfrac, "cpl": cpl, "amp": round(amp, 3),
                                "product_nrmse": round(pr, 4), "xor_acc": round(xa, 4)})
            if pr < best_p[0]: best_p = (pr, (pfrac, cpl))
            if xa > best_x[0]: best_x = (xa, (pfrac, cpl))
    out["best_product_nrmse"] = round(best_p[0], 4)
    out["best_xor_acc"] = round(best_x[0], 4)
    out["verdict"] = {
        "beats_linear_on_product": bool(best_p[0] < out["linear_product_nrmse"]),
        "beats_linear_on_xor": bool(best_x[0] - out["linear_xor_acc"] > 0.10),
    }
    print(json.dumps(out, indent=2))
    (RES/"ngspice_reservoir.json").write_text(json.dumps(out, indent=2)+"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
