"""ASSESS gate for Goal Loop memory candidates.

The Goal Loop should not silently turn every answer into durable memory. This
module stages a review receipt instead: result -> assessment -> candidate ->
explicit promotion later. The scoring is intentionally conservative and
stdlib-only; production promotion belongs behind a separate approval path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


RUBRIC_KEYS = ("accuracy", "usefulness", "freshness", "privacy", "source_quality")
PROMOTION_THRESHOLD = 0.75
# Autonomy (Balanced): below this floor on accuracy OR usefulness, a result is
# not even worth a human's review click — the assessment is still reported in
# the receipt, but surfaces skip staging it into the durable candidate queue.
# (This is what keeps the review console at "3 exceptions", not "50 digests".)
DISCARD_FLOOR = 0.5


def clears_auto_promotion(scores: dict[str, float]) -> bool:
    """Balanced auto-promotion bar: EVERY rubric dimension >= threshold.
    Callers must ALSO have independent verification (e.g. the scout's verify
    agent) before acting on this — scores alone never promote."""
    return all(float(scores.get(k, 0.0)) >= PROMOTION_THRESHOLD for k in RUBRIC_KEYS)


@dataclass(frozen=True)
class MemoryAssessment:
    """A non-durable candidate receipt for later human/system review."""

    content: str
    status: str
    scores: dict[str, float]
    decision: str
    promotion_ready: bool
    promotion_blocked_by: list[str] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)
    external_actions: str = "none"
    persisted: bool = False

    def as_event(self) -> dict[str, Any]:
        return {
            "type": "memory_candidate",
            "text": self.content,
            "status": self.status,
            "scores": dict(self.scores),
            "decision": self.decision,
            "promotionReady": self.promotion_ready,
            "promotionBlockedBy": list(self.promotion_blocked_by),
            "provenance": dict(self.provenance),
            "externalActions": self.external_actions,
            "persisted": self.persisted,
        }


def assess_result_for_memory(
    *,
    goal_text: str,
    result_text: str,
    goal_id: str,
    scope: str,
    confidence: float,
    tactic: str = "",
    model: str = "",
) -> MemoryAssessment:
    """Create an ASSESS receipt; never promote or persist durable memory."""

    content = f"Goal: {goal_text}\nAnswer: {result_text}".strip()
    scores = _score_candidate(goal_text=goal_text, result_text=result_text, confidence=confidence)
    blocked_by = [key for key in RUBRIC_KEYS if scores[key] < PROMOTION_THRESHOLD]
    if "approval" not in blocked_by:
        blocked_by.append("approval")
    # Weak results (low accuracy or usefulness) are reported but not staged:
    # they'd only bury the real candidates in the review queue.
    weak = scores["accuracy"] < DISCARD_FLOOR or scores["usefulness"] < DISCARD_FLOOR

    return MemoryAssessment(
        content=content,
        status="pending_assessment",
        scores=scores,
        decision="discard" if weak else "stage_for_review",
        promotion_ready=False,
        promotion_blocked_by=blocked_by,
        provenance={
            "goal_id": goal_id,
            "scope": scope,
            "tactic": tactic,
            "model": model,
            "confidence": confidence,
        },
    )


def _score_candidate(*, goal_text: str, result_text: str, confidence: float) -> dict[str, float]:
    """Cheap local rubric. It is a gate signal, not a truth oracle."""

    result = result_text.strip()
    has_answer = bool(result)
    has_context = bool(goal_text.strip())
    accuracy = _bounded(confidence if has_answer else 0.0)
    usefulness = 0.8 if has_answer and len(result.split()) >= 4 else (0.4 if has_answer else 0.0)
    freshness = 0.7 if has_context else 0.5
    privacy = 0.9 if not _looks_sensitive(result) else 0.35
    source_quality = 0.65  # Loop output is useful but unsourced until external verification is attached.
    return {
        "accuracy": accuracy,
        "usefulness": usefulness,
        "freshness": freshness,
        "privacy": privacy,
        "source_quality": source_quality,
    }


def _bounded(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _looks_sensitive(text: str) -> bool:
    lowered = text.lower()
    sensitive_markers = ("api key", "password", "secret", "token", "ssn", "credit card")
    return any(marker in lowered for marker in sensitive_markers)
