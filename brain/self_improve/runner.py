"""Self-improvement runner — the SAFE, GATED cycle that makes the brain's OWN
prompts smarter over time, without ever touching tenant-private data.

Privacy invariant (hard, by construction):
  - Eval cases are CURATED / representative / public — passed in by the caller.
    They are NEVER a tenant's private memories or questions, and this runner
    reads NO tenant scope. The system gets smarter from how it answers
    representative cases, not from anyone's private content.
  - Stage-only: the runner NEVER auto-promotes. It PROPOSES an improved prompt
    version (not activated) and returns a report; a human adopts it via
    registry.set_active. That human gate is the SelfImprover guardrail.

This is "the main brain gets smarter" step 1 (system self-improvement): zero
private surface, leverages the existing SelfImprover + prompt registry.
"""

from __future__ import annotations

from typing import Any

from self_improve.improver import SelfImprover
from self_improve.store import SelfImprovementStore


def _stage_only(_report: dict) -> bool:
    """Approval callback that always declines auto-promotion. Proposals are
    staged for a human to review and adopt — nothing self-promotes."""
    return False


async def run_improvement_cycle(
    prompt_ids: list[str],
    *,
    registry: Any,
    gateway: Any,
    eval_cases_by_prompt: dict[str, list[str]],
    tenant_id: str,
    workspace_id: str,
    store: SelfImprovementStore,
    max_eval: int = 3,
) -> list[dict]:
    """For each prompt, propose an improved variant and evaluate current vs
    candidate on its CURATED eval cases (LLM-as-judge). Returns staged reports
    (promoted is always False) for human review. Raises nothing per-prompt: a
    prompt with no curated cases is skipped with a note, so one gap never blocks
    the rest of the cycle."""
    si = SelfImprover(gateway, registry, max_eval=max_eval, store=store)
    reports: list[dict] = []
    for prompt_id in prompt_ids:
        cases = eval_cases_by_prompt.get(prompt_id) or []
        if not cases:
            reports.append({"prompt_id": prompt_id, "skipped": "no curated eval cases", "promoted": False})
            continue
        try:
            report = await si.improve(
                prompt_id, cases, approve=_stage_only,
                tenant_id=tenant_id, workspace_id=workspace_id,
            )
        except Exception as exc:  # one bad prompt must not abort the whole cycle
            reports.append({"prompt_id": prompt_id, "error": str(exc)[:200], "promoted": False})
            continue
        report["privacy"] = "curated_eval_cases_only; scoped ledger; no tenant-private content read"
        reports.append(report)
    return reports
