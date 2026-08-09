"""Prepende cockpit contracts.

This is the product-facing layer over the private Prepende overlay. It does not
write memory or execute actions; it names the Prepende scope, declares the lanes
the operator UI should show, and projects receipts into the six visible stages.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable

PUBLIC_PRODUCT_NAME = "Prepende"
PRIVATE_HARNESS_NAME = "Prepende Private Overlay"
PREPENDE_SCOPE = "prepende"

RECEIPT_STAGE_LABELS = ("Recalled", "Decided", "Proposed", "Blocked", "Verified", "Next")

ARTICLE_RUBRIC = (
    "clarity",
    "usefulness",
    "originality",
    "source_quality",
    "factual_safety",
    "voice_match",
    "user_relevance",
)

FEEDBACK_SIGNALS = (
    "thumbs",
    "comment",
    "correction",
    "save",
    "skip",
    "read_time",
    "more_like_this",
    "less_like_this",
)


@dataclass(frozen=True)
class CockpitLane:
    id: str
    label: str
    purpose: str
    write_policy: str
    public_policy: str


def cockpit_lanes() -> list[CockpitLane]:
    """Return the first-class Prepende lanes the cockpit should render."""
    return [
        CockpitLane(
            "memory",
            "Scoped Memory",
            "Approved tenant facts, preferences, and durable learnings.",
            "candidate-gated; explicit approval before durable preference writes",
            "private by default",
        ),
        CockpitLane(
            "vault",
            "Scoped Vault",
            "Source articles, research notes, reviewed material, and evidence pages.",
            "provenance required; public/private labels required at ingest",
            "export only approved public-safe excerpts",
        ),
        CockpitLane(
            "candidates",
            "Pending Candidates",
            "Facts, article preferences, and learning rules awaiting review.",
            "review queue; approve or reject, never silent promotion",
            "private operator surface",
        ),
        CockpitLane(
            "prediction_ledger",
            "Prediction Ledger",
            "Hash-locked forecasts with scoring rules, resolution dates, and calibration.",
            "append-only JSONL; no retroactive regime changes",
            "public-safe ledger rows can be exported after review",
        ),
        CockpitLane(
            "experiment_receipts",
            "Experiment Receipts",
            "Research runs, model checks, article assessments, and verification outputs.",
            "append-only receipts; failures preserved",
            "public only when bounded and reproducible",
        ),
        CockpitLane(
            "article_feedback",
            "Article Feedback",
            "User reactions, corrections, saves, skips, read-time, and qualitative notes.",
            "tenant-scoped; aggregate only with consent/anonymization",
            "never raw user feedback without approval",
        ),
        CockpitLane(
            "editorial_memory",
            "Editorial Memory",
            "Approved rules for what made articles better.",
            "preference candidates become durable only after approval",
            "private unless explicitly generalized",
        ),
        CockpitLane(
            "public_safe_exports",
            "Public-Safe Export Queue",
            "Bounded claims, reproducible results, and shareable Prepende artifacts.",
            "human-reviewed export gate",
            "only approved public-safe material leaves the lab",
        ),
        CockpitLane(
            "thought_bus",
            "Thought Bus",
            "Structured claims, evidence, risks, blockers, and memory candidates from agents.",
            "fusion before action; external actions still approval-gated",
            "receipts can be shared; raw private inputs stay scoped",
        ),
        CockpitLane(
            "provider_health",
            "Provider Health",
            "Model lane, memory lane, connector readiness, timeouts, and fallback status.",
            "read-only status receipts",
            "share only redacted health summaries",
        ),
    ]


def cockpit_manifest(scope: str = PREPENDE_SCOPE) -> dict[str, Any]:
    """Declarative manifest for the Prepende cockpit/scope."""
    return {
        "product": PUBLIC_PRODUCT_NAME,
        "scope": scope or PREPENDE_SCOPE,
        "privateHarness": PRIVATE_HARNESS_NAME,
        "positioning": (
            "Prepende is the product-facing brain. The private overlay keeps "
            "operator state separate, while user-facing learning is scoped, "
            "provenance-backed, candidate-gated, and receipt-visible."
        ),
        "receiptStages": list(RECEIPT_STAGE_LABELS),
        "lanes": [asdict(lane) for lane in cockpit_lanes()],
        "articleQualityLoop": {
            "principle": "Prepende should not just remember facts. It should remember what made the work better.",
            "rubric": list(ARTICLE_RUBRIC),
            "feedbackSignals": list(FEEDBACK_SIGNALS),
            "auditTrail": [
                "article version",
                "feedback received",
                "quality assessment",
                "preference candidate proposed",
                "approved or rejected learning",
                "next article changed because of X",
            ],
            "memoryPolicy": "candidate",
            "approvalGate": "Important preference changes remain pending until explicitly approved.",
            "privacy": "Raw user feedback stays tenant-scoped; global learning requires consent, anonymization, and provenance.",
        },
        "boundaries": [
            "Reviewed source material enters as labeled evidence, not vague AI training.",
            "User-specific preferences stay tenant-scoped unless the user opts into aggregation.",
            "Public Prepende exports must be bounded, reproducible, and public-safe.",
        ],
    }


def article_quality_receipt(
    *,
    article_id: str,
    version: str,
    feedback_received: Iterable[dict[str, Any]] = (),
    quality_assessment: dict[str, Any] | None = None,
    preference_candidate: str = "",
    learning_decision: str = "pending_review",
    next_article_changes: Iterable[str] = (),
    scope: str = PREPENDE_SCOPE,
) -> dict[str, Any]:
    """Build an auditable article-learning receipt without writing memory."""
    assessment = {name: None for name in ARTICLE_RUBRIC}
    if quality_assessment:
        for key, value in quality_assessment.items():
            if key in assessment:
                assessment[key] = value
            else:
                assessment.setdefault("notes", {})[key] = value
    candidate = None
    if preference_candidate.strip():
        candidate = {
            "content": preference_candidate.strip(),
            "status": "pending_assessment",
            "durableWrite": False,
            "promotionRequires": "explicit approval",
        }
    return {
        "product": PUBLIC_PRODUCT_NAME,
        "scope": scope or PREPENDE_SCOPE,
        "article": {"id": article_id, "version": version},
        "feedbackReceived": list(feedback_received),
        "qualityAssessment": assessment,
        "preferenceCandidate": candidate,
        "learningDecision": learning_decision,
        "nextArticleChangedBecause": list(next_article_changes),
        "memoryPolicy": "candidate",
        "externalActions": [],
        "actionExecuted": False,
    }


def _extract_receipt(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    brain = payload.get("brain")
    if isinstance(brain, dict) and isinstance(brain.get("receipt"), dict):
        return brain["receipt"]
    if isinstance(payload.get("receipt"), dict):
        return payload["receipt"]
    return payload


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _memory_summary(receipt: dict[str, Any]) -> list[str]:
    memory = receipt.get("memory") if isinstance(receipt.get("memory"), dict) else {}
    recall = receipt.get("recall") if isinstance(receipt.get("recall"), dict) else {}
    sources = recall.get("sources") if isinstance(recall.get("sources"), dict) else {}
    recalled = memory.get("recalled")
    lines: list[str] = []
    if recalled is not None:
        lines.append(f"{recalled} memory item(s) recalled")
    if sources:
        src = ", ".join(f"{key}: {value}" for key, value in sorted(sources.items()))
        lines.append(f"sources: {src}")
    if not lines:
        lines.append("No recall details reported.")
    return lines


def receipt_stage_view(payload: dict[str, Any]) -> dict[str, list[str]]:
    """Project a Prepende receipt into the six visible cockpit stages."""
    receipt = _extract_receipt(payload)
    view = {label: [] for label in RECEIPT_STAGE_LABELS}

    view["Recalled"].extend(_memory_summary(receipt))

    mode = receipt.get("mode") or receipt.get("status") or "unknown"
    tactic = receipt.get("tactic")
    model = receipt.get("model")
    decision = f"mode/status: {mode}"
    if tactic:
        decision += f"; tactic: {tactic}"
    if model:
        decision += f"; model: {model}"
    view["Decided"].append(decision)

    memory = receipt.get("memory") if isinstance(receipt.get("memory"), dict) else {}
    proposed = _as_list(memory.get("proposed"))
    if proposed:
        view["Proposed"].append(f"{len(proposed)} candidate learning item(s) proposed")
        for item in proposed[:3]:
            if isinstance(item, dict):
                content = str(item.get("content") or item.get("kind") or item)[:140]
            else:
                content = str(item)[:140]
            view["Proposed"].append(content)
    else:
        view["Proposed"].append("No candidate learning proposed.")

    errors = [str(receipt.get("error"))] if receipt.get("error") else []
    if payload.get("error") and isinstance(payload, dict):
        errors.append(str(payload["error"]))
    if payload.get("ok") is False and not errors:
        errors.append("Run did not complete successfully.")
    approval_required = bool(receipt.get("approvalRequired") or payload.get("approvalRequired"))
    if approval_required:
        errors.append("Approval required before external action.")
    if receipt.get("actionExecuted") is False or payload.get("actionExecuted") is False:
        errors.append("No external action executed.")
    view["Blocked"].extend(errors or ["No blocker reported."])

    verifier = receipt.get("verifier")
    if isinstance(verifier, dict):
        status = verifier.get("status", "unknown")
        repaired = verifier.get("repaired")
        detail = f"verifier: {status}"
        if repaired is not None:
            detail += f"; repaired: {bool(repaired)}"
        view["Verified"].append(detail)
    else:
        view["Verified"].append("No verifier details reported.")
    if receipt.get("actionExecuted") is False or payload.get("actionExecuted") is False:
        view["Verified"].append("Action safety verified: actionExecuted=false.")

    joined_errors = " ".join(errors).lower()
    if "timed out" in joined_errors or "timeout" in joined_errors:
        view["Next"].append("Retry with a healthy model lane; memory/status fallback remains the source of truth.")
    if proposed:
        view["Next"].append("Review proposed candidates before promoting any durable learning.")
    if approval_required:
        view["Next"].append("Approve or reject the pending action explicitly.")
    if not view["Next"]:
        view["Next"].append("No required next step reported by this receipt.")
    return view


def render_manifest_text(manifest: dict[str, Any]) -> str:
    lines = [
        f"{manifest['product']} cockpit",
        "=" * 64,
        f"scope: {manifest['scope']}",
        f"private harness: {manifest['privateHarness']}",
        "",
        manifest["positioning"],
        "",
        "lanes:",
    ]
    for lane in manifest["lanes"]:
        lines.append(f"- {lane['label']}: {lane['purpose']}")
    lines.extend(["", "article quality loop:"])
    loop = manifest["articleQualityLoop"]
    lines.append(loop["principle"])
    lines.append("rubric: " + ", ".join(loop["rubric"]))
    lines.append("audit trail: " + " -> ".join(loop["auditTrail"]))
    return "\n".join(lines)


def render_stage_view_text(view: dict[str, list[str]]) -> str:
    lines: list[str] = []
    for label in RECEIPT_STAGE_LABELS:
        lines.append(label)
        lines.append("-" * len(label))
        for item in view.get(label, []) or ["(empty)"]:
            lines.append(f"- {item}")
        lines.append("")
    return "\n".join(lines).rstrip()
