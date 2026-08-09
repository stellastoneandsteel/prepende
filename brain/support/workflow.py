"""Closed-loop support intake, diagnostics, dispatch, and deterministic receipts."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
import uuid
from html import escape
from typing import Any, Callable

from support.policy import classify, clean_text, issue_payload, safe_page_url
from support.store import default_support_store

_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def _enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def support_intake_tokens() -> dict[str, str]:
    raw = os.environ.get("PREPENDE_SUPPORT_INTAKE_TOKENS", "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {
        str(token).strip(): str(scope).strip()
        for token, scope in list(parsed.items())[:100]
        if str(token).strip() and str(scope).strip()
    }


def _target_urls(scope: str) -> tuple[str, ...]:
    """Return only host-owned diagnostic targets for this exact scope."""

    raw = os.environ.get("PREPENDE_SUPPORT_TARGETS", "").strip()
    if not raw:
        return ()
    try:
        parsed = json.loads(raw)
    except ValueError:
        return ()
    values = parsed.get(scope, ()) if isinstance(parsed, dict) else ()
    if isinstance(values, str):
        values = [values]
    targets: list[str] = []
    if isinstance(values, list):
        for value in values[:10]:
            safe = safe_page_url(value)
            if safe:
                targets.append(safe)
    return tuple(dict.fromkeys(targets))


def _default_probe(url: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        method="GET",
        headers={"user-agent": "Prepende-Support-Probe/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            return {"url": url, "reachable": True, "status": int(response.status)}
    except urllib.error.HTTPError as exc:
        return {"url": url, "reachable": True, "status": int(exc.code)}
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {"url": url, "reachable": False, "error": type(exc).__name__}


def run_diagnostics(
    scope: str,
    *,
    probe: Callable[[str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    targets = _target_urls(scope)
    checker = probe or _default_probe
    checks = [checker(url) for url in targets]
    healthy = bool(checks) and all(
        bool(item.get("reachable")) and 200 <= int(item.get("status") or 0) < 400
        for item in checks
    )
    return {
        "targetSource": "host_allowlist",
        "checks": checks,
        "healthy": healthy,
        "conclusion": (
            "configured targets passed"
            if healthy
            else "one or more configured targets failed"
            if checks
            else "no diagnostic targets configured"
        ),
        "customerUrlFetched": False,
    }


def _dispatch_issue(ticket: dict[str, Any]) -> dict[str, Any]:
    if not _enabled("PREPENDE_SUPPORT_AUTO_DISPATCH"):
        return {"attempted": False, "configured": False, "reason": "auto_dispatch_disabled"}
    token = os.environ.get("PREPENDE_SUPPORT_GITHUB_TOKEN", "").strip()
    repo = os.environ.get("PREPENDE_SUPPORT_GITHUB_REPO", "").strip()
    if not token or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        return {"attempted": False, "configured": False, "reason": "github_dispatch_unconfigured"}
    payload = json.dumps(issue_payload(ticket)).encode()
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/issues",
        data=payload,
        method="POST",
        headers={
            "accept": "application/vnd.github+json",
            "authorization": f"Bearer {token}",
            "content-type": "application/json",
            "user-agent": "Prepende-Support-Dispatcher/1.0",
            "x-github-api-version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            result = json.loads(response.read(64_000))
    except urllib.error.HTTPError as exc:
        return {"attempted": True, "configured": True, "ok": False, "status": exc.code}
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {"attempted": True, "configured": True, "ok": False, "error": type(exc).__name__}
    return {
        "attempted": True,
        "configured": True,
        "ok": True,
        "issueNumber": result.get("number"),
        "issueUrl": result.get("html_url"),
    }


def _send_ack(ticket: dict[str, Any]) -> dict[str, Any]:
    """Send one deterministic receipt under the explicitly scoped auto-policy."""

    if not _enabled("PREPENDE_SUPPORT_AUTO_EMAIL"):
        return {"attempted": False, "configured": False, "reason": "auto_email_disabled"}
    api_key = os.environ.get("PREPENDE_RESEND_API_KEY", "").strip()
    from_addr = os.environ.get("PREPENDE_EMAIL_FROM", "").strip()
    if not api_key or not from_addr:
        return {"attempted": False, "configured": False, "reason": "sender_unconfigured"}
    public = public_ticket_receipt(ticket)
    subject = f"Prepende support receipt {ticket['id']}"
    html = (
        "<p>We received your Prepende support report.</p>"
        f"<p><strong>Ticket:</strong> {escape(str(ticket['id']))}<br>"
        f"<strong>Status:</strong> {escape(str(public['status']))}<br>"
        f"<strong>Lane:</strong> {escape(str(public['lane']))}</p>"
        f"<p>{escape(str(public['next']))}</p>"
        "<p>This is an automated operational receipt. It does not expose internal logs, customer data, or credentials.</p>"
    )
    payload = json.dumps({
        "from": from_addr,
        "to": [ticket["email"]],
        "subject": subject,
        "html": html,
    }).encode()
    request = urllib.request.Request(
        "https://api.resend.com/emails",
        data=payload,
        method="POST",
        headers={
            "authorization": f"Bearer {api_key}",
            "content-type": "application/json",
            "user-agent": "Prepende-Support-Receipt/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            result = json.loads(response.read(32_000))
    except urllib.error.HTTPError as exc:
        return {"attempted": True, "configured": True, "ok": False, "status": exc.code}
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {"attempted": True, "configured": True, "ok": False, "error": type(exc).__name__}
    return {"attempted": True, "configured": True, "ok": True, "emailId": result.get("id")}


def public_ticket_receipt(ticket: dict[str, Any]) -> dict[str, Any]:
    category = str(ticket.get("category") or "unknown")
    risk_tier = str(ticket.get("riskTier") or "response_only")
    lane = str(ticket.get("lane") or "answer_or_route")
    next_by_lane = {
        "contain_and_explain": "Prepende will not autonomously change money, identity, privacy, security, secrets, DNS, or customer data. The receipt will explain the safe next step.",
        "diagnose_verify_repair": "Prepende will run only allowlisted diagnostics. If evidence supports a code fix, it will open a sanitized private repair task and verify the proposed patch.",
        "answer_or_route": "Prepende will return a bounded answer or an honest configuration blocker; no production mutation is authorized.",
    }
    return {
        "ok": True,
        "ticketId": ticket.get("id"),
        "tenantId": ticket.get("scope"),
        "status": ticket.get("status"),
        "category": category,
        "riskTier": risk_tier,
        "lane": lane,
        "next": next_by_lane.get(lane, next_by_lane["answer_or_route"]),
        "externalActions": "none",
        "actionExecuted": False,
        "containsCustomerText": False,
    }


async def create_support_ticket(
    scope: str,
    data: dict[str, Any],
    *,
    store: Any | None = None,
    probe: Callable[[str], dict[str, Any]] | None = None,
    dispatch: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    send_ack: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Persist and advance one ticket without model-dependent authority."""

    scope = clean_text(scope, limit=120)
    email = clean_text(data.get("email"), limit=254).lower()
    subject = clean_text(data.get("subject") or data.get("problem"), limit=180)
    description = clean_text(data.get("description") or data.get("details"), limit=6000)
    consent = str(data.get("contactConsent") or data.get("contact_consent") or "").lower()
    bot_field = clean_text(data.get("bot-field") or data.get("botField"), limit=100)
    if not scope:
        raise ValueError("support scope is missing")
    if bot_field:
        raise ValueError("submission refused")
    if not _EMAIL.fullmatch(email):
        raise ValueError("valid email is required")
    if not subject or not description:
        raise ValueError("subject and description are required")
    if consent not in {"1", "true", "yes", "on"}:
        raise ValueError("contact consent is required")

    policy = classify(subject, description)
    status = (
        "contained"
        if policy["lane"] == "contain_and_explain"
        else "diagnostic_pending"
        if policy["autoRepairAllowed"]
        else "response_pending"
    )
    seed = {
        "id": f"pst_{uuid.uuid4().hex[:16]}",
        "scope": scope,
        "email": email,
        "subject": subject,
        "description": description,
        "pageUrl": safe_page_url(data.get("pageUrl") or data.get("page_url")),
        "category": policy["category"],
        "riskTier": policy["riskTier"],
        "lane": policy["lane"],
        "status": status,
    }
    ledger = store or default_support_store()
    ticket = await ledger.create(seed)

    diagnostics: dict[str, Any] | None = None
    dispatch_receipt: dict[str, Any] | None = None
    if bool(policy["autoRepairAllowed"]):
        diagnostics = run_diagnostics(scope, probe=probe)
        ticket["diagnostics"] = diagnostics
        dispatch_receipt = (dispatch or _dispatch_issue)(ticket)
        if dispatch_receipt.get("ok"):
            status = "agent_dispatched"
        elif dispatch_receipt.get("attempted"):
            status = "dispatch_failed"
        else:
            status = "repair_queued"
        ticket = await ledger.update(
            ticket["id"],
            scope=scope,
            status=status,
            diagnostics=diagnostics,
            dispatch=dispatch_receipt,
        ) or ticket

    email_receipt = (send_ack or _send_ack)(ticket)
    public = public_ticket_receipt(ticket)
    actions: list[dict[str, Any]] = []
    if dispatch_receipt and dispatch_receipt.get("ok"):
        actions.append({"type": "private_repair_issue", "status": "created"})
    if email_receipt.get("ok"):
        actions.append({"type": "support_receipt_email", "status": "sent"})
    public["externalActions"] = actions or "none"
    public["actionExecuted"] = bool(actions)
    public["automation"] = {
        "diagnostics": "completed" if diagnostics is not None else "not_applicable",
        "repairDispatch": "created" if dispatch_receipt and dispatch_receipt.get("ok") else status,
        "emailReceipt": "sent" if email_receipt.get("ok") else email_receipt.get("reason") or "failed",
    }
    return public
