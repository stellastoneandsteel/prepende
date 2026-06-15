"""Resolvability — an axis ORTHOGONAL to calibration.

Calibration asks: when you say 70%, does it happen ~70% of the time? (accuracy on
the line.) Resolvability asks a different, perpendicular question: can a third party
even CHECK this claim — is there a concrete criterion, a specified regime, a
measurable claim? A prediction can be supremely confident and still unresolvable;
those are not miscalibrated, they are off the calibration plane entirely. This is the
guard that auto-flags unfalsifiable claims (e.g. "we will achieve the singularity")
before they pollute the ledger. (Surfaced by the night-cycle run, 2026-06-15.)
"""
from __future__ import annotations

import re
from typing import Any, Dict

_NUM = re.compile(r"\d")
_CRIT = re.compile(r"(>=|<=|>|<|=|%|\bif\b)", re.I)
_REGIME = re.compile(r"(n\s*[>=]|sample|trial|round|session|pair|blind|preregist|by\s+\d{4}|within|deadline|matched)", re.I)


def resolvability(contract) -> Dict[str, Any]:
    rule = (getattr(contract, "resolution_rule", "") or "").strip()
    regime = (getattr(contract, "eval_regime", "") or "").strip()
    claim = getattr(contract, "claim", {}) or {}
    kind = getattr(contract, "kind", "")

    checks = {
        "has_rule": len(rule) >= 8,
        "rule_has_criterion": ("y=1" in rule.replace(" ", "")) or (bool(_CRIT.search(rule)) and bool(_NUM.search(rule))),
        "has_regime": len(regime) >= 8,
        "regime_specific": bool(_REGIME.search(regime)) or bool(_NUM.search(regime)),
    }
    if kind == "probability":
        checks["claim_concrete"] = isinstance(claim.get("p"), (int, float))
    elif kind == "numeric":
        checks["claim_concrete"] = ("value" in claim and "lo" in claim and "hi" in claim)
    elif kind == "categorical":
        checks["claim_concrete"] = "label" in claim
    else:
        checks["claim_concrete"] = False

    # the load-bearing checks (a checkable criterion + a specific regime) dominate;
    # merely non-empty rule/regime text earns almost nothing, so vague claims fail.
    weights = {"has_rule": 0.05, "rule_has_criterion": 0.40,
               "has_regime": 0.05, "regime_specific": 0.30, "claim_concrete": 0.20}
    score = round(sum(weights[k] for k, v in checks.items() if v), 2)
    return {
        "score": round(score, 2),
        "checks": checks,
        "flags": [k for k, v in checks.items() if not v],
        "verdict": "resolvable" if score >= 0.6 else "WEAK — may be unfalsifiable",
    }


def resolvability_report(ledger) -> str:
    L = ["RESOLVABILITY (orthogonal axis: can a third party CHECK it?)",
         "-" * 62]
    weak = 0
    for c, _ in ledger.records():
        r = resolvability(c)
        if r["score"] < 0.6:
            weak += 1
        mark = "ok " if r["score"] >= 0.6 else "!! "
        L.append("%s%.2f  %s" % (mark, r["score"], (c.question or "")[:54]))
    L.append("-" * 62)
    L.append("%d locked prediction(s) flagged WEAK (<0.60) — review before trusting." % weak)
    L.append("note: high conviction + low resolvability = the singularity trap. A claim")
    L.append("can be perfectly calibrated in theory and still be uncheckable in practice.")
    return "\n".join(L)
