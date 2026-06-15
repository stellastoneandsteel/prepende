"""Reliability-diagram SVG (no dependencies)."""
from __future__ import annotations

from .metrics import reliability


def reliability_svg(resolved, path: str, nbins: int = 10,
                    title: str = "Calibration / reliability diagram") -> str:
    rel = reliability(resolved, nbins)
    W = H = 440
    m = 64
    plot = W - 2 * m

    def X(p): return m + p * plot
    def Y(v): return H - m - v * plot

    P = []
    P.append('<svg viewBox="0 0 %d %d" xmlns="http://www.w3.org/2000/svg" font-family="sans-serif">' % (W, H))
    P.append('<rect x="0" y="0" width="%d" height="%d" fill="white"/>' % (W, H))
    P.append('<rect x="%d" y="%d" width="%d" height="%d" fill="none" stroke="#999"/>' % (m, m, plot, plot))
    P.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#ccc" stroke-dasharray="5 4"/>' % (X(0), Y(0), X(1), Y(1)))
    P.append('<text x="%d" y="%d" font-size="11" fill="#999" text-anchor="end" transform="rotate(-90 %d %d)">perfect calibration</text>' % (X(0.62) - 4, Y(0.62) - 6, X(0.62) - 4, Y(0.62) - 6))
    for t in (0.0, 0.25, 0.5, 0.75, 1.0):
        P.append('<text x="%.0f" y="%d" font-size="11" fill="#555" text-anchor="middle">%.2f</text>' % (X(t), H - m + 18, t))
        P.append('<text x="%d" y="%.0f" font-size="11" fill="#555" text-anchor="end">%.2f</text>' % (m - 8, Y(t) + 4, t))
    P.append('<text x="%d" y="%d" font-size="12" fill="#333" text-anchor="middle">predicted probability</text>' % (m + plot / 2, H - 14))
    P.append('<text x="18" y="%d" font-size="12" fill="#333" text-anchor="middle" transform="rotate(-90 18 %d)">observed frequency</text>' % (m + plot / 2, m + plot / 2))
    P.append('<text x="%d" y="30" font-size="15" fill="#222" text-anchor="middle">%s</text>' % (W / 2, title))
    if rel.get("n"):
        pts = [b for b in rel["bins"] if b["n"]]
        if len(pts) >= 2:
            poly = " ".join("%.1f,%.1f" % (X(b["mean_pred"]), Y(b["observed"])) for b in pts)
            P.append('<polyline points="%s" fill="none" stroke="#534AB7" stroke-width="2"/>' % poly)
        for b in pts:
            cx, cy = X(b["mean_pred"]), Y(b["observed"])
            P.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#7F77DD"/>' % (cx, Y(b["ci"][0]), cx, Y(b["ci"][1])))
            P.append('<circle cx="%.1f" cy="%.1f" r="%.1f" fill="#534AB7" fill-opacity="0.75"/>' % (cx, cy, 3 + min(10, b["n"])))
        skill = (1 - rel["brier"] / rel["uncertainty"]) if rel["uncertainty"] > 0 else 0.0
        P.append('<text x="%d" y="%d" font-size="12" fill="#222">ECE=%.3f   Brier=%.3f   skill=%+.3f</text>' % (m + 6, m + 18, rel["ece"], rel["brier"], skill))
        P.append('<text x="%d" y="%d" font-size="11" fill="#666">n=%d   point size ~ count   bars = 95%% Wilson CI</text>' % (m + 6, m + 34, rel["n"]))
    else:
        P.append('<text x="%d" y="%d" font-size="12" fill="#999" text-anchor="middle">no resolved predictions yet</text>' % (W / 2, H / 2))
    P.append('</svg>')
    svg = "\n".join(P)
    with open(path, "w", encoding="utf-8") as f:
        f.write(svg)
    return path
