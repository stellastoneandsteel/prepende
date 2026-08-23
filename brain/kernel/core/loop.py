"""GoalLoop — the heart of Prepende, the OS scheduler for thought.

Take a goal, decide how to think (Strategist), pursue it (Tactic), collapse to
one decisive result (Resolver), and leave a real deliverable in the Workspace.
Phase 0 is the thin-but-real version of the loop; memory, durability, richer
tactics, self-organization, and self-improvement layer in by later phases
without changing this shape.

`run` drives a single goal and reports progress through an async `on_event`
callback so any surface (TUI, REPL, later a remote API) can render it live:
    {"type": "status"|"token"|"artifact"|"done"|"error", "text": ...}
plus one truthful run receipt near the end:
    {"type": "receipt", "receipt": {mode, loopUsed, tactic, verifier,
     memory: {proposed, written}, externalActions, actionExecuted, ...}}
`run` also returns that receipt.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from kernel.core.memory_assess import assess_result_for_memory
from kernel.core.recall import unified_recall
from kernel.core.thinking_voice import status_event
from kernel.core.types import Goal
from kernel.core.verifier import LOW_CONFIDENCE
from models.provenance import model_provenance

Event = dict[str, Any]
OnEvent = Callable[[Event], Awaitable[None]]


class GoalLoop:
    def __init__(self, gateway: Any, strategist: Any, workspace: Any, memory: Any = None, scope: str = "default", workspace_id: str | None = None, connectors: Any = None, runs: Any = None, knowledge: Any = None, verifier: Any = None, memory_policy: str = "candidate", vault_recall: bool = False, graphify: Any = None) -> None:
        self.gateway = gateway
        self.strategist = strategist
        self.workspace = workspace
        self.memory = memory
        self.scope = scope
        self.workspace_id = workspace_id or scope
        self.connectors = connectors  # outbound hub; tactics reach tools/products through this
        self.runs = runs  # durable run journal; goals survive a crash and can resume
        self.knowledge = knowledge  # the self-organizing wiki/vault (curated knowledge)
        self.verifier = verifier  # optional ResultVerifier; receipt says "skipped" when absent
        # "candidate" (default, fail-safe): Assess gate — the loop only PROPOSES
        # memory in the receipt; nothing durable is written until an approved path
        # writes it explicitly. "diary" (owner-brain autonomy, Balanced): same
        # candidate gating for semantic facts, PLUS an always-on episodic diary
        # line per run — short, decay-weighted, archivable; the brain's
        # autobiography, never its beliefs. "auto" (explicit opt-in for TUI/dev
        # surfaces only): the loop writes its goal/answer memory itself.
        self.memory_policy = memory_policy
        # Vault-aware recall is opt-in. build_brain enables it for the owner's
        # vault; hosted loops enable it only after resolving a tenant-isolated
        # namespace through ScopedVaults (kernel/core/recall.py).
        self.vault_recall = vault_recall
        # Optional audited graph projection for the owner brain only. Hosted
        # tenant loops intentionally leave this unset.
        self.graphify = graphify

    async def run(self, goal_text: str, on_event: OnEvent, history: list | None = None) -> Event:
        goal = Goal(text=goal_text)
        await self.workspace.open(goal.id)
        if self.runs is not None:
            self.runs.start(goal.id, goal_text)  # durable: marked 'running' until done/failed
        await self.workspace.progress(goal.id, f"Received goal: {goal_text}")
        await on_event(status_event("goal.received", f"goal {goal.id} received", context=goal_text))

        # 0. Recall — what does the brain already know? (Compounding memory.)
        # One unified associative read: scoped memory, plus — owner brain only —
        # the vault's RAG hits and a budgeted one-hop wikilink-graph walk out
        # from the matched pages (kernel/core/recall.py).
        recalled: list = []
        recall_sources: dict = {}
        recall_selection: dict = {}
        if self.memory is not None or (self.vault_recall and self.knowledge is not None):
            await on_event(status_event("memory.searching", "checking scoped memory", context=goal_text))
            try:
                rec = await unified_recall(goal_text, memory=self.memory, knowledge=self.knowledge,
                                           graphify=self.graphify, scope=self.scope, k=5,
                                           vault=self.vault_recall)
                recalled = list(rec["items"])
                recall_sources = dict(rec["sources"])
                recall_selection = dict(rec.get("selection") or {})
            except Exception:
                recalled, recall_sources, recall_selection = [], {}, {}
            if recalled:
                detail = f"Recalled {len(recalled)} item(s)"
                if self.vault_recall:
                    detail += (f" (memory {recall_sources.get('memory', 0)}, wiki {recall_sources.get('vault', 0)},"
                               f" graph {recall_sources.get('graphNeighbors', 0)},"
                               f" graphify {recall_sources.get('graphify', 0)})")
                await self.workspace.progress(goal.id, detail)
                await on_event(status_event("memory.recalled", detail.lower(), context=goal_text))

        # 1. Decide HOW to think. Pass the orchestration registry (if attached) so
        # the Strategist can LOG registry metadata for the tactic it picks — this
        # never changes which tactic is selected (no registry -> unchanged).
        await on_event(status_event("strategy.choosing", "choosing tactic", context=goal_text))
        choice = await self.strategist.choose(goal, {"registry": getattr(self, "registry", None)})
        tactic_name = getattr(choice.tactic, "name", "?")
        model_name = getattr(self.gateway, "name", "?")
        await self.workspace.progress(goal.id, f"Strategist chose tactic '{tactic_name}' on model '{model_name}'")
        await on_event(status_event("strategy.chosen", f"strategist -> {tactic_name} · model {model_name}", context=goal_text))
        # Routing receipt: surface the chosen tactic's registry metadata (readiness,
        # external actions, approval, estimate) without changing execution.
        _meta = getattr(choice, "budget", None) or {}
        if _meta.get("registryEntryId"):
            await self.workspace.progress(goal.id, f"Registry: {_meta['registryEntryId']} readiness={_meta.get('readiness')} externalActions={_meta.get('externalActions')} approvalRequired={_meta.get('approvalRequired')}")
            await on_event(status_event("registry.chosen", f"registry -> {_meta['registryEntryId']} · {_meta.get('readiness')} · externalActions={_meta.get('externalActions')}", context=goal_text))

        # The truthful run receipt. The loop itself never executes external
        # actions — those go through the approval-gated connectors/workflow lane.
        receipt: Event = {
            "goalId": goal.id,
            "mode": "goal_loop",
            "loopUsed": True,
            "tactic": tactic_name,
            "model": model_name,
            "modelProvenance": model_provenance(self.gateway).as_dict(),
            "agentsInvoked": [tactic_name],
            "budget": dict(choice.budget or {}),
            "verifier": {"status": "skipped"},
            "memory": {"recalled": len(recalled), "proposed": [], "written": []},
            **({"recall": {
                "sources": recall_sources,
                **({"selection": recall_selection} if recall_selection else {}),
            }} if recall_sources else {}),
            "externalActions": [],
            "actionExecuted": False,
        }

        # 2. Pursue it (tokens stream out via emit). 3. Collapse to one result.
        async def emit(kind: str, data: Any) -> None:
            await on_event({"type": kind, "text": data})

        try:
            await on_event(status_event("tactic.running", f"running tactic {tactic_name}", context=goal_text))
            candidates = await choice.tactic.run(goal, {
                "emit": emit, "goal": goal, "memory": recalled,
                "connectors": self.connectors, "history": history or [],
                "tenant_id": self.scope, "workspace_id": self.workspace_id,
            })
            result = await choice.resolver.resolve(candidates, goal)
        except Exception as exc:  # surface failures instead of crashing the surface
            await self.workspace.progress(goal.id, f"ERROR: {type(exc).__name__}: {exc}")
            if self.runs is not None:
                self.runs.fail(goal.id, f"{type(exc).__name__}: {exc}")
            error_event = {"type": "error", "text": f"{type(exc).__name__}: {exc}"}
            voice_event = status_event("run.error", "run failed", context=str(exc))
            if "thinkingVoice" in voice_event:
                error_event["thinkingVoice"] = voice_event["thinkingVoice"]
            await on_event(error_event)
            receipt["error"] = f"{type(exc).__name__}: {exc}"
            receipt["modelProvenance"] = model_provenance(self.gateway).as_dict()
            await on_event({"type": "receipt", "receipt": receipt})
            return receipt

        # Everything past this point runs AFTER an answer exists (verify,
        # artifact, assess, journal close). A failure here — a disk fault while
        # writing the artifact, a raising surface callback, journal contention —
        # must not leave the run stuck as 'running' with no receipt, so it takes
        # the same error path as the tactic phase above.
        journal_closed = False  # once runs.finish lands, never flip 'done' to 'failed'
        try:
            # 3b. Verify — production verifiers may gate themselves by tactic
            # so cheap solo turns stay cheap. Injected verifiers without a
            # policy method retain the original always-run behavior.
            run_verifier = self.verifier is not None
            if run_verifier:
                policy = getattr(self.verifier, "should_verify", None)
                if callable(policy):
                    try:
                        run_verifier = bool(policy(tactic_name))
                    except Exception as exc:
                        run_verifier = False
                        receipt["verifier"] = {
                            "status": "unverified",
                            "confidence": None,
                            "verdict": "policy_error",
                            "critique": f"{type(exc).__name__}: {exc}"[:200],
                        }
                if not run_verifier and receipt["verifier"]["status"] == "skipped":
                    if getattr(self.verifier, "mode", "") == "off":
                        reason = "disabled_by_policy"
                    elif tactic_name == "solo":
                        reason = "solo_opt_in_required"
                    else:
                        reason = "tactic_not_enabled"
                    receipt["verifier"] = {"status": "skipped", "reason": reason}

            # A low-confidence, schema-valid panel verdict earns ONE repair.
            if run_verifier:
                try:
                    verdict = await self.verifier.verify(goal_text, result.text)
                except Exception as exc:
                    verdict = {"status": "unverified", "confidence": None, "verdict": "verifier_error", "critique": str(exc)[:200]}
                confidence = verdict.get("confidence")
                # Repair only when the verifier BOTH judged the answer weak/fail
                # AND scored it below the threshold. A missing confidence (None)
                # or a 'pass' verdict must never trigger a rewrite of an answer
                # the verifier judged fine.
                if (verdict.get("status") == "verified"
                        and verdict.get("verdict") in ("weak", "fail")
                        and isinstance(confidence, (int, float)) and confidence < LOW_CONFIDENCE):
                    await on_event(status_event("verify.repairing", "low confidence — one repair pass", context=goal_text))
                    try:
                        repaired = await self.gateway.complete([{"role": "user", "content": (
                            f"Goal: {goal_text}\n\nDraft answer:\n{result.text}\n\n"
                            f"Reviewer critique: {verdict.get('critique') or 'low confidence'}\n\n"
                            "Rewrite the answer to fully address the critique. Return only the improved answer."
                        )}], max_tokens=1024)
                        result.text = str(repaired).strip() or result.text
                        verdict = dict(verdict, repaired=True, repairAttempts=1)
                    except Exception:
                        verdict = dict(verdict, repaired=False, repairAttempts=1)
                receipt["verifier"] = verdict

            # Refresh after all generation/verification calls. Subscription and
            # API adapters record provider-local fallbacks per call; the final
            # receipt must report what actually answered, not only what was asked.
            receipt["modelProvenance"] = model_provenance(self.gateway).as_dict()

            # 4. Leave a real deliverable in the workspace.
            await on_event(status_event("artifact.writing", "writing artifact", context=goal_text))
            artifact = await self.workspace.write_artifact(
                goal.id, "answer.md", f"# Goal\n\n{goal_text}\n\n# Answer\n\n{result.text}\n"
            )
            await self.workspace.progress(goal.id, f"Wrote artifact: {artifact}")
            await on_event({"type": "artifact", "text": artifact})

            # 5. Assess before memory. The loop stages a candidate receipt; under the
            # "candidate" policy (the product surface), it never silently writes
            # durable memory. Promotion is a later explicit approval path.
            if self.memory is not None:
                assessment = assess_result_for_memory(
                    goal_text=goal_text,
                    result_text=result.text,
                    goal_id=goal.id,
                    scope=self.scope,
                    confidence=getattr(result, "confidence", 0.0),
                    tactic=getattr(result, "tactic", tactic_name),
                    model=getattr(result, "model", model_name),
                )
                proposal = {
                    "kind": "goal_answer",
                    "content": assessment.content[:1000],
                    "assessment": {
                        "status": assessment.status,
                        "decision": assessment.decision,
                        "promotionReady": assessment.promotion_ready,
                        "promotionBlockedBy": list(assessment.promotion_blocked_by),
                        "scores": dict(assessment.scores),
                    },
                }
                receipt["memory"]["proposed"].append(proposal)
                await self.workspace.progress(
                    goal.id,
                    "Memory candidate staged for ASSESS; durable write blocked until approval",
                )
                await on_event(assessment.as_event())
                if self.memory_policy == "auto":
                    try:
                        await on_event(status_event("memory.writing", "writing scoped memory", context=goal_text))
                        memory_id = await self.memory.write(
                            f"Goal: {goal_text}\nAnswer: {result.text}",
                            scope=self.scope,
                            metadata={"goal_id": goal.id},
                        )
                        receipt["memory"]["written"].append({"id": memory_id, "kind": "goal_answer"})
                    except Exception:
                        pass
                elif self.memory_policy == "diary":
                    # Episodic autobiography: one short "what happened" row per run.
                    # kind=episodic keeps it decay-weighted in recall and archivable
                    # in bulk; it is a record of events, never a promoted belief —
                    # semantic facts still go through the candidate gate above.
                    try:
                        diary = (
                            f"[diary] {goal_text[:180]} -> {getattr(result, 'tactic', tactic_name)}"
                            f" via {getattr(result, 'model', model_name)},"
                            f" confidence {getattr(result, 'confidence', 0.0):.2f}"
                        )
                        diary_id = await self.memory.write(
                            diary,
                            scope=self.scope,
                            metadata={"kind": "episodic", "source": "goal_loop.diary", "goal_id": goal.id},
                        )
                        receipt["memory"]["written"].append({"id": diary_id, "kind": "episodic_diary"})
                        await on_event(status_event("memory.writing", "episodic diary written; semantic memory stays Assess-gated", context=goal_text))
                    except Exception:
                        pass
                else:
                    await on_event(status_event("memory.writing", "memory proposed as candidate (Assess-gated)", context=goal_text))

            if self.runs is not None:
                self.runs.finish(goal.id, result.text)  # durable: run complete
                journal_closed = True
            await on_event(status_event("run.done", "run complete", context=goal_text))
            await on_event({"type": "receipt", "receipt": receipt})
            await on_event({"type": "done", "text": result.text})
            return receipt
        except Exception as exc:  # post-answer failures still owe the surface a receipt
            msg = f"{type(exc).__name__}: {exc}"
            # Journal FIRST: if the workspace/disk is the failing piece, the
            # progress write below will raise too, and the row must not be left
            # 'running' (it would be misreported as a crash survivor at startup).
            if self.runs is not None and not journal_closed:
                self.runs.fail(goal.id, msg)
            try:
                await self.workspace.progress(goal.id, f"ERROR: {msg}")
            except Exception:
                pass  # best effort — the workspace itself may be what failed
            error_event = {"type": "error", "text": msg}
            voice_event = status_event("run.error", "run failed", context=str(exc))
            if "thinkingVoice" in voice_event:
                error_event["thinkingVoice"] = voice_event["thinkingVoice"]
            await on_event(error_event)
            receipt["error"] = msg
            receipt["modelProvenance"] = model_provenance(self.gateway).as_dict()
            await on_event({"type": "receipt", "receipt": receipt})
            return receipt
