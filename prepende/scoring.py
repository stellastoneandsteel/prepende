"""Proper scoring rules + calibration over resolved predictions."""
from __future__ import annotations

import math
from typing import Any, Dict, List, Tuple


def brier(p: float, y: int) -> float:
    return (p - y) ** 2


def log_score(p: float, y: int, eps: float = 1e-9) -> float:
    p = min(1 - eps, max(eps, p))
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def _prob_pairs(resolved) -> List[Tuple[float, int]]:
    pairs = []
    for c, r in resolved:
        if r is None:
            continue
        outcome = getattr(r, "outcome", None)
        if not isinstance(outcome, dict):
            continue
        if c.kind == "probability":
            pairs.append((float(c.claim.get("p")), int(outcome.get("y"))))
        elif c.kind == "categorical":
            correct = 1 if str(c.claim.get("label")) == str(outcome.get("label")) else 0
            pairs.append((float(c.claim.get("p", 0.5)), correct))
    return pairs


def summary(resolved) -> Dict[str, Any]:
    pairs = _prob_pairs(resolved)
    out: Dict[str, Any] = {"n_prob": len(pairs)}
    if pairs:
        n = len(pairs)
        mb = sum(brier(p, y) for p, y in pairs) / n
        ml = sum(log_score(p, y) for p, y in pairs) / n
        base = sum(y for _, y in pairs) / n
        baseline_brier = sum((base - y) ** 2 for _, y in pairs) / n
        skill = 1 - mb / baseline_brier if baseline_brier > 0 else float("nan")
        out.update({"brier": mb, "log_loss": ml, "base_rate": base,
                    "baseline_brier": baseline_brier, "skill": skill})
    return out


def calibration_table(resolved, nbins: int = 5) -> List[Dict[str, Any]]:
    pairs = _prob_pairs(resolved)
    table = []
    for b in range(nbins):
        lo, hi = b / nbins, (b + 1) / nbins
        inb = [(p, y) for p, y in pairs if p >= lo and (p < hi or (b == nbins - 1 and p <= hi))]
        if inb:
            table.append({"bin": "%.1f-%.1f" % (lo, hi), "n": len(inb),
                          "mean_pred": sum(p for p, _ in inb) / len(inb),
                          "observed": sum(y for _, y in inb) / len(inb)})
        else:
            table.append({"bin": "%.1f-%.1f" % (lo, hi), "n": 0, "mean_pred": None, "observed": None})
    return table


def numeric_summary(resolved) -> Dict[str, Any]:
    errs, cover, cnt = [], 0, 0
    for c, r in resolved:
        outcome = getattr(r, "outcome", None) if r is not None else None
        if not isinstance(outcome, dict) or c.kind != "numeric":
            continue
        cnt += 1
        pred, obs = float(c.claim.get("value")), float(outcome.get("value"))
        errs.append(abs(pred - obs))
        lo, hi = c.claim.get("lo"), c.claim.get("hi")
        if lo is not None and hi is not None and float(lo) <= obs <= float(hi):
            cover += 1
    if cnt == 0:
        return {"n_numeric": 0}
    return {"n_numeric": cnt, "mae": sum(errs) / cnt, "ci_coverage": cover / cnt}


def penalized_probability_summary(records) -> Dict[str, Any]:
    """Brier mean with locked forfeit penalties included in the denominator."""
    losses: List[float] = []
    resolved = forfeited = 0
    for contract, terminal in records:
        if contract.kind not in {"probability", "categorical"}:
            continue
        outcome = getattr(terminal, "outcome", None) if terminal is not None else None
        if isinstance(outcome, dict):
            if contract.kind == "probability":
                y = int(outcome["y"])
            else:
                y = int(str(contract.claim["label"]) == str(outcome["label"]))
            losses.append(brier(float(contract.claim["p"]), y))
            resolved += 1
        elif getattr(terminal, "disposition", None) == "forfeit":
            losses.append(float(contract.nonresolution_policy["penalty"]))
            forfeited += 1
    return {
        "n_scored": len(losses),
        "n_resolved": resolved,
        "n_forfeited": forfeited,
        "penalized_brier": (sum(losses) / len(losses)) if losses else None,
    }


def penalized_numeric_summary(records) -> Dict[str, Any]:
    """Numeric absolute error with locked forfeit penalties in the denominator."""
    losses: List[float] = []
    resolved = forfeited = 0
    for contract, terminal in records:
        if contract.kind != "numeric":
            continue
        outcome = getattr(terminal, "outcome", None) if terminal is not None else None
        if isinstance(outcome, dict):
            losses.append(abs(float(contract.claim["value"]) - float(outcome["value"])))
            resolved += 1
        elif getattr(terminal, "disposition", None) == "forfeit":
            losses.append(float(contract.nonresolution_policy["penalty"]))
            forfeited += 1
    return {
        "n_scored": len(losses),
        "n_resolved": resolved,
        "n_forfeited": forfeited,
        "penalized_mae": (sum(losses) / len(losses)) if losses else None,
    }
