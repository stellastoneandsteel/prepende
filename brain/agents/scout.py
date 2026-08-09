"""KnowledgeScout — coordinates the gathering agents + the human-approval gate.

This is the Golden-Loop knowledge slice:
    Research -> Verify -> credible & >=0.75 -> AUTO-promote (provenance, reversible)
                       -> anything less    -> pending_review -> [human] accept/reject

Autonomy (Balanced, owner decision 2026-07-02): findings the verify agent
independently rates 'credible' at high credibility promote themselves — that is
the compounding the brain exists for. Everything below the bar still stops at
pending_review; `accept()` remains the only door either way, so every promotion
(auto or human) carries the same provenance and is supersede-reversible.

Watchtower is run-on-demand here (a digest you trigger), not a live daemon — a
true background scheduler is a deploy concern (cron/worker), noted in
KNOWLEDGE-AGENTS.md.
"""

from __future__ import annotations

from typing import Any

from agents.research import ResearchAgent
from agents.verify import SourceVerifyAgent


class KnowledgeScout:
    def __init__(self, gateway: Any, items: Any, memory: Any = None, knowledge: Any = None, scope: str = "default") -> None:
        self.gateway = gateway
        self.items = items
        self.memory = memory          # promote accepted items here
        self.knowledge = knowledge    # and into the vault wiki
        self.scope = scope
        self.research_agent = ResearchAgent(gateway, items)
        self.verify_agent = SourceVerifyAgent(gateway, items)

    # Balanced autonomy bar: verify must say 'credible' AND credibility >= this
    # for a finding to promote itself. Tunable per deployment; auto_promote=False
    # restores the everything-gated behavior.
    AUTO_PROMOTE_CREDIBILITY = 0.75

    async def research(self, topic: str, projects: list[str] | None = None,
                       auto_promote: bool = True) -> dict[str, Any]:
        """Gather + verify a topic. Verified-credible findings auto-promote
        (Balanced bar); everything else lands in pending_review."""
        r = await self.research_agent.research(topic, scope=self.scope, projects=projects)
        v = await self.verify_agent.verify(r["item_id"])
        out: dict[str, Any] = {**r, "verdict": v.get("verdict"),
                               "credibility": v.get("credibility"), "auto_promoted": False}
        cred = float(v.get("credibility") or 0.0)
        if auto_promote and v.get("verdict") == "credible" and cred >= self.AUTO_PROMOTE_CREDIBILITY:
            acc = await self.accept(r["item_id"])
            out["auto_promoted"] = bool(acc.get("ok"))
            out["promoted_to"] = acc.get("promoted_to", [])
            if not acc.get("ok"):
                out["promote_errors"] = acc.get("errors", [])
        return out

    def pending(self) -> list[dict[str, Any]]:
        """Items awaiting your review — the 'reviewed intelligence', not raw links."""
        return list(self.items.list(scope=self.scope, state="pending_review"))

    async def accept(self, item_id: str) -> dict[str, Any]:
        """The human gate. Promote an item into durable memory + the vault, with provenance."""
        item = self.items.get(item_id)
        if not item:
            return {"ok": False, "error": "unknown item"}
        prov = f"[source: {item.get('source_url') or item.get('author') or 'n/a'}]"
        body = f"{item.get('summary','')}\nClaims: {', '.join(item.get('claims', []))} {prov}"
        promoted: list[str] = []
        errors: list[str] = []
        if self.memory is not None:
            try:
                await self.memory.write(f"Knowledge ({item.get('topic')}): {body}", scope=self.scope,
                                        metadata={"knowledge_item": item_id, "source": item.get("source_url")})
                promoted.append("memory")
            except Exception as e:
                errors.append(f"memory: {e}")
        if self.knowledge is not None:
            try:
                await self.knowledge.ingest(f"# {item.get('title')}\n\n{body}\n\nTopic: {item.get('topic')}")
                promoted.append("vault")
            except Exception as e:
                errors.append(f"vault: {e}")
        if errors:
            # accept() is the ONLY path into the durable layers, so marking the
            # item accepted while a write failed would silently drop approved
            # knowledge from the review queue with a false success report. Keep
            # it pending_review and tell the caller what failed; a retry may
            # re-write an already-promoted target, which is the safer duplicate.
            return {"ok": False, "item_id": item_id, "state": "pending_review",
                    "promoted_to": promoted, "errors": errors}
        self.items.set_state(item_id, "accepted")
        return {"ok": True, "item_id": item_id, "promoted_to": promoted}

    def reject(self, item_id: str) -> dict[str, Any]:
        if not self.items.get(item_id):
            return {"ok": False, "error": "unknown item"}
        self.items.set_state(item_id, "rejected")
        return {"ok": True, "item_id": item_id, "state": "rejected"}
