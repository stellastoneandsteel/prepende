"""Calibration metrics: ECE, MCE, Brier decomposition, Wilson intervals."""
from __future__ import annotations

import math
from typing import Any, Dict, List, Tuple

from .scoring import _prob_pairs, brier


def wilson(k: int, n: int, z: float = 1.96) -> Tuple[float, float]:
    """95% Wilson score interval for k successes in n trials."""
    if n == 0:
        return (0.0, 0.0)
    phat = k / n
    denom = 1 + z * z / n
    center = (phat + z * z / (2 * n)) / denom
    half = z * math.sqrt(phat * (1 - phat) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def reliability(resolved, nbins: int = 10) -> Dict[str, Any]:
    """Binned calibration analysis with ECE/MCE and the Murphy Brier decomposition.

    Brier = reliability - resolution + uncertainty (lower reliability is better,
    higher resolution is better). Returns per-bin stats with Wilson CIs.
    """
    pairs = _prob_pairs(resolved)
    n = len(pairs)
    out: Dict[str, Any] = {"n": n, "nbins": nbins, "bins": []}
    if n == 0:
        return out
    base = sum(y for _, y in pairs) / n
    ece = mce = reliab = resol = 0.0
    bins: List[Dict[str, Any]] = []
    for b in range(nbins):
        lo, hi = b / nbins, (b + 1) / nbins
        inb = [(p, y) for p, y in pairs if p >= lo and (p < hi or (b == nbins - 1 and p <= hi))]
        nb = len(inb)
        if nb:
            mp = sum(p for p, _ in inb) / nb
            of = sum(y for _, y in inb) / nb
            k = sum(y for _, y in inb)
            gap = abs(mp - of)
            ece += (nb / n) * gap
            mce = max(mce, gap)
            reliab += nb * (mp - of) ** 2
            resol += nb * (of - base) ** 2
            lo_ci, hi_ci = wilson(k, nb)
            bins.append({"lo": lo, "hi": hi, "n": nb, "mean_pred": mp, "observed": of, "ci": [lo_ci, hi_ci]})
        else:
            bins.append({"lo": lo, "hi": hi, "n": 0, "mean_pred": None, "observed": None, "ci": [0.0, 0.0]})
    reliab /= n
    resol /= n
    uncertainty = base * (1 - base)
    out.update({
        "base_rate": base, "ece": ece, "mce": mce,
        "reliability": reliab, "resolution": resol, "uncertainty": uncertainty,
        "brier": sum(brier(p, y) for p, y in pairs) / n,
        "bins": bins,
    })
    return out
