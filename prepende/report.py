"""Human-readable, evidence-floor-aware reports."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from .canonical import digest
from .ledger import IntegrityReport
from .scoring import (
    numeric_summary,
    penalized_numeric_summary,
    penalized_probability_summary,
    summary,
)
from .metrics import reliability


MIN_CALIBRATION_N = 30


def _integrity(ledger, verify_kwargs: dict[str, Any]) -> dict[str, Any]:
    value = ledger.integrity(**verify_kwargs) if verify_kwargs else ledger.integrity()
    if isinstance(value, IntegrityReport):
        return value.to_dict()
    return value


def cohort_key(contract) -> tuple[str, ...]:
    evaluation = getattr(contract, "evaluation", None)
    evaluator = getattr(contract, "evaluator", None)
    protocol = "prepende/2" if isinstance(evaluation, dict) else "prepende/1"
    evaluation_manifest = (
        digest("evaluation", evaluation) if isinstance(evaluation, dict) else "legacy-unspecified"
    )
    return (
        protocol,
        str(getattr(contract, "stream_id", "legacy-unregistered")),
        str(contract.predictor),
        str(getattr(contract, "model_version", "legacy-unspecified")),
        str(getattr(contract, "domain", "legacy-unspecified")),
        str(getattr(contract, "kind", "legacy-unspecified")),
        str(getattr(contract, "eval_regime", "legacy-unspecified")),
        str(evaluation.get("spec_digest", "legacy-unspecified")) if evaluation else "legacy-unspecified",
        evaluation_manifest,
        digest("evaluator", evaluator) if isinstance(evaluator, dict) else "legacy-unspecified",
        str(getattr(contract, "provenance", "legacy-unspecified")),
        "signed" if getattr(contract, "resolver_policy", {}).get("mode") == "signed" else "self",
        digest("resolver-policy", contract.resolver_policy)
        if isinstance(getattr(contract, "resolver_policy", None), dict) else "legacy-unspecified",
        digest("nonresolution-policy", contract.nonresolution_policy)
        if isinstance(getattr(contract, "nonresolution_policy", None), dict) else "legacy-unspecified",
        digest("void-policy", contract.void_policy)
        if isinstance(getattr(contract, "void_policy", None), dict) else "legacy-unspecified",
    )


def segregated_records(records) -> dict[tuple[str, ...], list[Any]]:
    """Partition records by every field that may change metric meaning."""
    groups: dict[tuple[str, ...], list[Any]] = defaultdict(list)
    for record in records:
        groups[cohort_key(record[0])].append(record)
    return dict(groups)


def grouped_summaries(records, *, minimum_n: int = MIN_CALIBRATION_N) -> list[dict[str, Any]]:
    minimum_n = max(MIN_CALIBRATION_N, int(minimum_n))
    groups = segregated_records(records)
    out = []
    for key, group in sorted(groups.items()):
        probabilistic = summary(group)
        item = {
            "protocol": key[0], "stream_id": key[1], "predictor": key[2],
            "model_version": key[3], "domain": key[4], "kind": key[5],
            "evaluation_regime": key[6], "evaluation_digest": key[7],
            "evaluation_manifest_digest": key[8], "evaluator_digest": key[9],
            "provenance": key[10], "resolver_class": key[11],
            "resolver_policy_digest": key[12], "nonresolution_policy_digest": key[13],
            "void_policy_digest": key[14],
            "locked": len(group),
            "resolved": sum(isinstance(getattr(terminal, "outcome", None), dict) for _, terminal in group),
            "forfeited": sum(getattr(terminal, "disposition", None) == "forfeit" for _, terminal in group),
            "void": sum(getattr(terminal, "disposition", None) == "void" for _, terminal in group),
            "pending": sum(terminal is None for _, terminal in group),
            "n_prob": probabilistic.get("n_prob", 0),
            "evidence_status": "SUFFICIENT" if probabilistic.get("n_prob", 0) >= minimum_n else "INSUFFICIENT_EVIDENCE",
            "penalized": penalized_probability_summary(group),
            "numeric": numeric_summary(group),
            "numeric_penalized": penalized_numeric_summary(group),
        }
        if item["evidence_status"] == "SUFFICIENT":
            item["calibration"] = probabilistic
            item["reliability"] = reliability(group, nbins=5)
        out.append(item)
    return out


def build_report(ledger, *, minimum_n: int = MIN_CALIBRATION_N, **verify_kwargs: Any) -> str:
    minimum_n = max(MIN_CALIBRATION_N, int(minimum_n))
    records = ledger.records()
    integrity = _integrity(ledger, verify_kwargs)
    terminal = [terminal for _, terminal in records if terminal is not None]
    resolved = [item for item in records if isinstance(getattr(item[1], "outcome", None), dict)]
    forfeited = sum(getattr(item, "disposition", None) == "forfeit" for item in terminal)
    voided = sum(getattr(item, "disposition", None) == "void" for item in terminal)
    locked = len(records)
    pending = sum(item is None for _, item in records)
    lines = [
        "PREPENDE PROTOCOL AUDIT REPORT",
        "=" * 64,
        f"protocol             : {integrity.get('protocol', 'unknown')}",
        f"verification status  : {integrity.get('status', 'unknown')}",
        f"internal chain       : {'VALID' if integrity.get('internally_valid') else 'TAMPERED'}",
        f"trusted anchor       : {'YES' if integrity.get('anchored') else 'NO'}",
        f"complete through     : {integrity.get('complete_through')}",
        f"independent resolver : {'YES' if integrity.get('independently_resolved') else 'NO'}",
        f"locked predictions   : {locked}",
        f"resolved             : {len(resolved)}",
        f"pending              : {pending}",
        f"unresolved rate      : {(pending / locked if locked else 0):.1%}",
        f"forfeited            : {forfeited}",
        f"forfeit rate         : {(forfeited / locked if locked else 0):.1%}",
        f"void                 : {voided}",
    ]
    overdue = integrity.get("overdue") or []
    if overdue:
        lines.append(f"overdue unresolved   : {len(overdue)}")
    unwitnessed = integrity.get("unwitnessed_terminals") or []
    if unwitnessed:
        lines.append(f"unwitnessed terminal: {len(unwitnessed)}")
    if integrity.get("warnings"):
        lines.extend(["", "verification warnings:"] + [f"  - {item}" for item in integrity["warnings"]])
    if integrity.get("errors"):
        lines.extend(["", "verification errors:"] + [f"  - {item}" for item in integrity["errors"]])

    lines.extend(["", "segregated evidence cohorts:"])
    for group in grouped_summaries(records, minimum_n=minimum_n):
        lines.append(
            "  {protocol} | {stream_id} | {predictor} | {model_version} | {domain} | {kind} | {provenance} | {resolver_class} | {evaluation_regime} {evaluation_digest} {evaluation_manifest_digest} {evaluator_digest} {resolver_policy_digest} {nonresolution_policy_digest} {void_policy_digest}".format(**group)
        )
        lines.append(
            "    locked={locked} resolved={resolved} pending={pending} ({pending_rate:.1%}) forfeited={forfeited} ({forfeit_rate:.1%}) void={void}".format(
                **group,
                pending_rate=group["pending"] / group["locked"] if group["locked"] else 0,
                forfeit_rate=group["forfeited"] / group["locked"] if group["locked"] else 0,
            )
        )
        if group["evidence_status"] != "SUFFICIENT":
            lines.append(
                f"    INSUFFICIENT_EVIDENCE: n_prob={group['n_prob']} < {minimum_n}; calibration curve and skill suppressed"
            )
        else:
            calibration = group["calibration"]
            rel = group["reliability"]
            lines.append(
                "    Brier={:.3f} log_loss={:.3f} ECE={:.3f} MCE={:.3f}".format(
                    calibration["brier"], calibration["log_loss"], rel["ece"], rel["mce"]
                )
            )
        penalized = group["penalized"]
        if penalized["n_forfeited"]:
            lines.append(
                "    locked-forfeit penalized Brier={:.3f} over {} scored".format(
                    penalized["penalized_brier"], penalized["n_scored"]
                )
            )
        numeric_penalized = group["numeric_penalized"]
        numeric = group["numeric"]
        if numeric.get("n_numeric"):
            lines.append(
                "    numeric n={} MAE={:.3f} CI coverage={:.1%}".format(
                    numeric["n_numeric"], numeric["mae"], numeric["ci_coverage"]
                )
            )
        if numeric_penalized["n_forfeited"]:
            lines.append(
                "    locked-forfeit penalized MAE={:.3f} over {} scored".format(
                    numeric_penalized["penalized_mae"], numeric_penalized["n_scored"]
                )
            )

    lines.extend([
        "",
        "claim boundary: internal chaining detects edits, reordering, and interior deletion.",
        "External completeness is only established for a registered stream through a",
        "checkpoint signed by a verifier-trusted authority. Hidden unregistered streams",
        "remain outside what a local ledger can prove.",
    ])
    return "\n".join(lines)
