"""SelfImprover — the gated propose -> evaluate -> commit -> rollback loop.

The brain improves its OWN prompts, safely:
  1. propose  — generate an improved variant as a NEW version (NOT activated).
  2. evaluate — run current vs candidate on eval cases; LLM-as-judge tallies wins.
  3. gate     — promote only if the candidate wins AND a human approves (required).
  4. commit   — flip the active-version pointer. rollback() flips it back.

Guardrails (immutable, not agent-writable):
  - the `approve` callback is REQUIRED — no silent self-promotion.
  - scoped to PROMPT artifacts only — never code, never these guardrails.
  - hard cap on eval calls (prevents runaway cost / "agentic DoS").
"""

from __future__ import annotations

from typing import Any, Callable

from kernel.core.scope import ScopeIdentity
from self_improve.store import SelfImprovementStore


class SelfImprover:
    def __init__(
        self,
        gateway: Any,
        registry: Any,
        max_eval: int = 3,
        *,
        store: SelfImprovementStore | None = None,
    ) -> None:
        self.gateway = gateway
        self.reg = registry
        self.max_eval = max_eval  # hard ceiling on eval calls
        self.store = store

    def _ledger(self) -> SelfImprovementStore:
        if self.store is None:
            raise RuntimeError("a SelfImprovementStore is required for scoped self-improvement")
        return self.store

    @staticmethod
    def _scope(tenant_id: str, workspace_id: str) -> ScopeIdentity:
        return ScopeIdentity(tenant_id=tenant_id, workspace_id=workspace_id)

    async def propose(
        self,
        prompt_id: str,
        *,
        tenant_id: str,
        workspace_id: str,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        scope = self._scope(tenant_id, workspace_id)
        ledger = self._ledger()
        created_run = run_id is None
        run = ledger.start_run(scope, prompt_id) if created_run else {"id": run_id}
        try:
            current_version = self.reg.active_version(prompt_id, scope.prompt_scope)
            current = await self.reg.get(prompt_id, scope=scope.prompt_scope)
            if not current:
                raise ValueError(f"prompt '{prompt_id}' has no active text")
            improved = await self.gateway.complete(
                [{"role": "user", "content":
                    "Improve this prompt so it produces clearer, more useful results. "
                    "Return ONLY the improved prompt text, no commentary.\n\nPrompt:\n" + current}],
                max_tokens=600,
            )
            candidate_version = self.reg.add_version(prompt_id, improved.strip())
            candidate = ledger.stage_candidate(
                str(run["id"]), scope, prompt_id=prompt_id,
                previous_version=current_version, candidate_version=candidate_version,
            )
            report = {
                "runId": str(run["id"]),
                "candidateId": candidate["id"],
                "prompt_id": prompt_id,
                "prev": current_version,
                "candidate": candidate_version,
                **scope.as_dict(),
            }
            if created_run:
                ledger.finish_run(str(run["id"]), scope, status="staged")
            return report
        except Exception as exc:
            if created_run:
                ledger.finish_run(str(run["id"]), scope, status="failed", error=str(exc))
            raise

    async def _run(self, prompt_text: str, case: str) -> str:
        content = prompt_text.format(input=case) if "{input}" in prompt_text else f"{prompt_text}\n\n{case}"
        return await self.gateway.complete([{"role": "user", "content": content}], max_tokens=300)

    async def _judge(self, task: str, a: str, b: str) -> str:
        out = await self.gateway.complete(
            [{"role": "user", "content":
                f"Task: {task}\n\nAnswer A:\n{a}\n\nAnswer B:\n{b}\n\n"
                "Which answer is better? Reply with exactly one letter: A or B."}],
            max_tokens=4,
        )
        return "B" if "B" in out.upper()[:4] else "A"

    async def evaluate(
        self,
        candidate_id: str,
        eval_cases: list[str],
        *,
        tenant_id: str,
        workspace_id: str,
    ) -> tuple[int, int]:
        scope = self._scope(tenant_id, workspace_id)
        row = self._ledger().get_candidate(candidate_id, scope)
        if row is None:
            raise ValueError("candidate not found in this tenant/workspace")
        prompt_id = str(row["prompt_id"])
        current = await self.reg.get(prompt_id, scope=scope.prompt_scope)
        candidate = self.reg.version_text(prompt_id, str(row["candidate_version"]))
        cases = eval_cases[: self.max_eval]
        wins = 0
        for case in cases:
            a = await self._run(current, case)
            b = await self._run(candidate, case)
            if await self._judge(case, a, b) == "B":
                wins += 1
        self._ledger().record_evaluation(candidate_id, scope, wins=wins, total=len(cases))
        return wins, len(cases)

    async def promote(
        self,
        candidate_id: str,
        *,
        tenant_id: str,
        workspace_id: str,
        approved_by: str,
    ) -> dict[str, Any]:
        """The only activation door: exact-scope candidate + explicit approver."""
        scope = self._scope(tenant_id, workspace_id)
        ledger = self._ledger()
        candidate = ledger.get_candidate(candidate_id, scope)
        if candidate is None:
            raise ValueError("candidate not found in this tenant/workspace")
        current = self.reg.active_version(str(candidate["prompt_id"]), scope.prompt_scope)
        if current != candidate["previous_version"]:
            raise ValueError("active prompt changed after evaluation; refusing stale promotion")
        approval = ledger.approve_candidate(candidate_id, scope, approved_by=approved_by)
        request = ledger.request_promotion(candidate_id, str(approval["id"]), scope)
        try:
            await self.reg.set_active(
                str(candidate["prompt_id"]), str(candidate["candidate_version"]),
                scope=scope.prompt_scope,
            )
        except Exception as exc:
            ledger.finish_promotion(str(request["id"]), scope, promoted=False, reason=str(exc))
            raise
        promotion = ledger.finish_promotion(str(request["id"]), scope, promoted=True)
        return {
            "promoted": True,
            "candidateId": candidate_id,
            "approvalId": approval["id"],
            "promotionRequestId": promotion["id"],
            "prompt_id": candidate["prompt_id"],
            "candidate": candidate["candidate_version"],
            **scope.as_dict(),
        }

    async def improve(
        self,
        prompt_id: str,
        eval_cases: list[str],
        approve: Callable[[dict], bool],
        *,
        tenant_id: str,
        workspace_id: str,
        approved_by: str = "",
    ) -> dict:
        if approve is None:
            raise ValueError("approve callback is required (human-in-the-loop guardrail)")
        scope = self._scope(tenant_id, workspace_id)
        ledger = self._ledger()
        run = ledger.start_run(scope, prompt_id)
        try:
            proposal = await self.propose(
                prompt_id, tenant_id=tenant_id, workspace_id=workspace_id, run_id=str(run["id"])
            )
            wins, total = await self.evaluate(
                str(proposal["candidateId"]), eval_cases,
                tenant_id=tenant_id, workspace_id=workspace_id,
            )
        except Exception as exc:
            ledger.finish_run(str(run["id"]), scope, status="failed", error=str(exc))
            raise
        candidate_better = total > 0 and wins > total / 2
        report = {
            **proposal,
            "wins": wins,
            "total": total,
            "candidate_better": candidate_better,
            "promoted": False,
        }
        try:
            approved = bool(approve(report)) if candidate_better else False
        except Exception as exc:
            ledger.finish_run(str(run["id"]), scope, status="failed", error=str(exc))
            raise
        if approved:
            if not approved_by.strip():
                ledger.finish_run(
                    str(run["id"]), scope, status="failed",
                    error="approved_by required when approval callback returns true",
                )
                raise ValueError("approved_by is required when approval callback returns true")
            try:
                report.update(await self.promote(
                    str(proposal["candidateId"]), tenant_id=tenant_id,
                    workspace_id=workspace_id, approved_by=approved_by,
                ))
            except Exception as exc:
                ledger.finish_run(str(run["id"]), scope, status="failed", error=str(exc))
                raise
        ledger.finish_run(
            str(run["id"]), scope, status="promoted" if report["promoted"] else "staged"
        )
        return report

    async def rollback(
        self,
        prompt_id: str,
        to_version: str | None = None,
        *,
        tenant_id: str,
        workspace_id: str,
        approved_by: str,
    ) -> str:
        scope = self._scope(tenant_id, workspace_id)
        ledger = self._ledger()
        vs = self.reg.versions(prompt_id)
        if not vs:
            raise ValueError(f"prompt '{prompt_id}' has no versions")
        current = self.reg.active_version(prompt_id, scope.prompt_scope)
        alternatives = [v for v in vs if v != current]
        target = to_version or (alternatives[-1] if alternatives else current)
        if target not in vs:
            raise ValueError(f"unknown prompt version: {target}")
        run = ledger.start_run(scope, prompt_id)
        candidate = ledger.stage_candidate(
            str(run["id"]), scope, prompt_id=prompt_id,
            previous_version=current, candidate_version=target, kind="rollback",
        )
        try:
            await self.promote(
                str(candidate["id"]), tenant_id=tenant_id,
                workspace_id=workspace_id, approved_by=approved_by,
            )
        except Exception as exc:
            ledger.finish_run(str(run["id"]), scope, status="failed", error=str(exc))
            raise
        ledger.finish_run(str(run["id"]), scope, status="promoted")
        return target
