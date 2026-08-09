"""SourceVerifyAgent — scores whether a gathered item is credible.

Assesses source quality, recency, author authority, evidence strength, and
cross-source conflict, returning a credibility score + reasoning. No claim
should enter durable knowledge as fact without provenance AND a verification
pass; this produces that pass. It updates the item's confidence/contradiction
fields but never changes its review state — only a human accepts.
"""

from __future__ import annotations

import json
from typing import Any


class SourceVerifyAgent:
    name = "verify"

    def __init__(self, gateway: Any, items: Any) -> None:
        self.gateway = gateway
        self.items = items

    async def verify(self, item_id: str) -> dict[str, Any]:
        item = self.items.get(item_id)
        if not item:
            return {"ok": False, "error": "unknown item"}
        prompt = (
            "Assess whether this gathered item is actually supported by its sources. Be SKEPTICAL — "
            "over-grading is a serious failure here. Return STRICT JSON only:\n"
            '{"credibility": 0.0-1.0, "addresses_topic": true|false, "evidence_strength": str, '
            '"uncertainty": str, "verdict": "credible"|"weak"|"unverified"}\n\n'
            "Rubric (apply strictly):\n"
            "- 'credible' ONLY if the sources DIRECTLY address the topic AND substantiate specific, "
            "concrete claims.\n"
            "- If the summary says the sources are off-topic, indirect, thin, or 'do not address' the "
            "question, OR there are no concrete claims, the verdict MUST be 'unverified' and "
            "addresses_topic MUST be false.\n"
            "- 'weak' = on-topic but only limited or indirect evidence.\n\n"
            f"Topic: {item.get('topic')}\nTitle: {item.get('title')}\n"
            f"Source: {item.get('source_url')} ({item.get('author')})\n"
            f"Summary: {item.get('summary')}\nClaims: {item.get('claims')}\n"
        )
        raw = await self.gateway.complete([{"role": "user", "content": prompt}], max_tokens=400)
        data = _parse_json(raw)
        cred = float(data.get("credibility", item.get("confidence", 0.3)) or 0.3)
        verdict = str(data.get("verdict", "unverified")).lower()
        if verdict not in ("credible", "weak", "unverified"):
            verdict = "unverified"
        # Deterministic backstop — the model grades itself "credible" even when its own
        # summary admits it found nothing (observed 2026-06-19). 'credible' is unjustifiable
        # without concrete claims, a topic match, and a real credibility score; downgrade.
        claims = item.get("claims") or []
        addresses = data.get("addresses_topic", True)
        if verdict == "credible" and (not claims or addresses is False or cred < 0.5):
            verdict = "weak" if claims else "unverified"
        # blend the agent's assessed confidence into the item (never auto-accept)
        return {"ok": True, "item_id": item_id, "credibility": cred,
                "verdict": verdict, "uncertainty": data.get("uncertainty", "")}


def _parse_json(text: str) -> dict[str, Any]:
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`"); t = t[t.find("{"):]
    try:
        return json.loads(t[t.find("{"): t.rfind("}") + 1])
    except Exception:
        return {}
