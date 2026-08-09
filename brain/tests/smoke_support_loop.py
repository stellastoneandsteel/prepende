"""Offline proof for the bounded, tenant-scoped Prepende support loop."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from support.policy import issue_payload, sanitize_for_agent
from support.store import SupportStore
from support.workflow import create_support_ticket


def main() -> None:
    root = Path(tempfile.mkdtemp(prefix="prepende-support-"))
    store = SupportStore(str(root / "support.db"))
    os.environ["PREPENDE_SUPPORT_TARGETS"] = json.dumps({
        "tenant-a": ["https://prepende.example/", "https://api.prepende.example/v1/health"],
        "other": ["https://other.example/"],
    })

    probes: list[str] = []
    dispatches: list[dict] = []
    emails: list[dict] = []

    def probe(url: str) -> dict:
        probes.append(url)
        return {"url": url, "reachable": True, "status": 200}

    def dispatch(ticket: dict) -> dict:
        payload = issue_payload(ticket)
        dispatches.append(payload)
        return {"attempted": True, "configured": True, "ok": True, "issueNumber": 42}

    def email(ticket: dict) -> dict:
        emails.append(ticket)
        return {"attempted": True, "configured": True, "ok": True, "emailId": "em_test"}

    receipt = asyncio.run(create_support_ticket("tenant-a", {
        "email": "customer@example.com",
        "subject": "The Prepende page is broken",
        "description": "I get a 500. Token token-abcdefghijklmnop and email customer@example.com.",
        "pageUrl": "https://prepende.example/dashboard?secret=yes#fragment",
        "contactConsent": "yes",
    }, store=store, probe=probe, dispatch=dispatch, send_ack=email))
    assert receipt["status"] == "agent_dispatched", receipt
    assert receipt["actionExecuted"] is True, receipt
    assert probes == [
        "https://prepende.example/",
        "https://api.prepende.example/v1/health",
    ], probes
    assert len(dispatches) == 1 and len(emails) == 1
    issue_text = json.dumps(dispatches[0])
    assert "customer@example.com" not in issue_text
    assert "token-abcdefghijklmnop" not in issue_text
    assert "?secret" not in issue_text

    tickets = asyncio.run(store.list(scope="tenant-a"))
    assert len(tickets) == 1 and tickets[0]["email"] == "customer@example.com", tickets
    assert asyncio.run(store.list(scope="other")) == [], "tenant support ticket leaked"
    assert tickets[0]["diagnostics"]["customerUrlFetched"] is False

    contained = asyncio.run(create_support_ticket("tenant-a", {
        "email": "owner@example.com",
        "subject": "Please refund my subscription",
        "description": "I was charged and want the billing record changed.",
        "contactConsent": True,
    }, store=store, probe=probe, dispatch=dispatch, send_ack=email))
    assert contained["status"] == "contained", contained
    assert contained["riskTier"] == "owner_controlled", contained
    assert len(dispatches) == 1, "billing request was sent to the code repair agent"

    try:
        asyncio.run(create_support_ticket("tenant-a", {
            "email": "bad",
            "subject": "broken",
            "description": "broken",
            "contactConsent": True,
        }, store=store, probe=probe, dispatch=dispatch, send_ack=email))
    except ValueError as exc:
        assert "email" in str(exc)
    else:
        raise AssertionError("invalid support email was accepted")

    sanitized = sanitize_for_agent("@copilot use Bearer top.secret.value and a@b.example.com")
    assert "@copilot" not in sanitized and "a@b.example.com" not in sanitized and "top.secret" not in sanitized

    print("SUPPORT LOOP SMOKE: OK")
    print("  tenant scope, PII redaction, host-only probes, repair dispatch, containment: verified")


if __name__ == "__main__":
    main()
