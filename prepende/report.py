"""Human-readable calibration report for a ledger."""
from __future__ import annotations

from .scoring import summary, numeric_summary
from .metrics import reliability


def build_report(ledger) -> str:
    recs = ledger.records()
    resolved = [(c, r) for c, r in recs if r is not None]
    bad = ledger.integrity()
    L = []
    L.append("PREPENDE CALIBRATION LEDGER")
    L.append("=" * 64)
    L.append("locked predictions : %d" % len(recs))
    L.append("resolved           : %d" % len(resolved))
    L.append("pending            : %d" % (len(recs) - len(resolved)))
    L.append("anti-retrofit chain: %s" % ("OK (all locked hashes verify)" if not bad else "TAMPERED: %s" % bad))

    s = summary(resolved)
    if s.get("n_prob"):
        rel = reliability(resolved, nbins=5)
        L.append("")
        L.append("probabilistic forecasts: %d" % s["n_prob"])
        L.append("  Brier        : %.3f   (lower is better; 0.25 = coin flip)" % s["brier"])
        L.append("  baseline     : %.3f   (always predict base rate %.2f)" % (s["baseline_brier"], s["base_rate"]))
        L.append("  skill score  : %+.3f   (>0 beats the base-rate baseline)" % s["skill"])
        L.append("  log loss     : %.3f" % s["log_loss"])
        L.append("  ECE / MCE    : %.3f / %.3f   (calibration error; 0 = perfect)" % (rel["ece"], rel["mce"]))
        L.append("  Brier decomp : reliability %.3f - resolution %.3f + uncertainty %.3f"
                 % (rel["reliability"], rel["resolution"], rel["uncertainty"]))
        L.append("")
        L.append("  reliability table (well-calibrated => mean_pred ~ observed; CI = 95%% Wilson):")
        L.append("    bin        n    mean_pred  observed   95%% CI")
        for b in rel["bins"]:
            if b["n"]:
                L.append("    %.1f-%.1f   %3d     %.2f      %.2f      [%.2f, %.2f]"
                         % (b["lo"], b["hi"], b["n"], b["mean_pred"], b["observed"], b["ci"][0], b["ci"][1]))
            else:
                L.append("    %.1f-%.1f     0      -         -" % (b["lo"], b["hi"]))

    nm = numeric_summary(resolved)
    if nm.get("n_numeric"):
        L.append("")
        L.append("numeric forecasts: %d" % nm["n_numeric"])
        L.append("  MAE          : %.3f" % nm["mae"])
        L.append("  CI coverage  : %.0f%%   (should match the stated CI level)" % (nm["ci_coverage"] * 100))

    L.append("")
    L.append("honest limits: this makes predictions FALSIFIABLE and SCORABLE, not correct.")
    L.append("calibration is only meaningful at n>=30 across independent domains; self-scoring")
    L.append("is weak evidence to outsiders — commit this ledger to git (or an external")
    L.append("timestamp service) so each lock provably precedes its outcome.")
    return "\n".join(L)
