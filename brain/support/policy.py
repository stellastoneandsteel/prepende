"""Deterministic policy for the humanless Prepende support loop.

Customer text is untrusted input. It may describe a problem, but it can never
grant the repair agent more authority. This module classifies the request and
returns the only automation lane it is allowed to enter.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_BEARER = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}", re.I)
_SECRET = re.compile(
    r"\b(?:sk|rk|gh[opusr]|xox[baprs]|key|token)[-_][A-Za-z0-9._-]{8,}\b",
    re.I,
)

_CATEGORY_TERMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("security", ("security", "vulnerability", "breach", "hacked", "exploit", "xss", "injection")),
    ("privacy", ("privacy", "delete my data", "data deletion", "gdpr", "personal data")),
    ("billing", ("billing", "charged", "charge", "refund", "invoice", "subscription", "payment")),
    ("identity", ("login", "sign in", "account", "password", "email change", "locked out", "auth")),
    ("outage", ("down", "outage", "offline", "unavailable", "500", "502", "503", "504")),
    ("performance", ("slow", "timeout", "latency", "hang", "stuck", "loading")),
    ("bug", ("bug", "broken", "error", "wrong", "doesn't work", "does not work", "fix")),
    ("access", ("access", "invite", "onboard", "trial", "early access")),
    ("question", ("question", "how do", "how can", "what is", "can i")),
)

_CONTAINED = {"security", "privacy", "billing", "identity"}
_REPAIRABLE = {"outage", "performance", "bug"}


def clean_text(value: object, *, limit: int) -> str:
    text = str(value or "").replace("\x00", " ")
    text = re.sub(r"[\t\r ]+", " ", text)
    text = re.sub(r"\n{4,}", "\n\n\n", text).strip()
    return text[:limit]


def safe_page_url(value: object) -> str:
    """Keep a user-visible HTTPS page path while dropping queries/fragments.

    The value is evidence only. Diagnostics never fetch this URL; they use a
    host-owned allowlist so a support report cannot become an SSRF request.
    """

    raw = clean_text(value, limit=2048)
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return ""
    if parsed.scheme != "https" or not parsed.hostname:
        return ""
    host = parsed.hostname.encode("idna").decode("ascii").lower()
    port = f":{parsed.port}" if parsed.port and parsed.port != 443 else ""
    path = parsed.path if parsed.path.startswith("/") else "/"
    return urlunsplit(("https", host + port, path[:1024], "", ""))


def classify(subject: str, description: str) -> dict[str, object]:
    haystack = f"{subject}\n{description}".lower()
    category = "unknown"
    for candidate, terms in _CATEGORY_TERMS:
        if any(term in haystack for term in terms):
            category = candidate
            break

    if category in _CONTAINED:
        return {
            "category": category,
            "riskTier": "owner_controlled",
            "lane": "contain_and_explain",
            "autoRepairAllowed": False,
            "next": "No autonomous account, money, identity, privacy, or security mutation is allowed.",
        }
    if category in _REPAIRABLE:
        return {
            "category": category,
            "riskTier": "bounded_repair",
            "lane": "diagnose_verify_repair",
            "autoRepairAllowed": True,
            "next": "Run allowlisted diagnostics, then dispatch a sanitized private repair issue if code work is supported by evidence.",
        }
    return {
        "category": category,
        "riskTier": "response_only",
        "lane": "answer_or_route",
        "autoRepairAllowed": False,
        "next": "Return a deterministic help receipt; do not mutate production.",
    }


def sanitize_for_agent(value: object, *, limit: int = 4000) -> str:
    """Remove common PII/secrets before customer text reaches a coding agent."""

    text = clean_text(value, limit=limit * 2)
    text = _EMAIL.sub("[email redacted]", text)
    text = _BEARER.sub("Bearer [redacted]", text)
    text = _SECRET.sub("[credential redacted]", text)
    # Prevent a report from addressing bots/people or embedding active HTML.
    text = re.sub(r"(?<![\w.])@([A-Za-z0-9_-]+)", r"[mention:\1]", text)
    text = text.replace("<", "&lt;").replace(">", "&gt;")
    return text[:limit]


def issue_payload(ticket: dict[str, object]) -> dict[str, object]:
    """Build the private, PII-minimized issue consumed by the repair workflow."""

    title = sanitize_for_agent(ticket.get("subject"), limit=120) or "Prepende support report"
    description = sanitize_for_agent(ticket.get("description"), limit=4000)
    page_url = safe_page_url(ticket.get("pageUrl"))
    policy = ticket.get("policy") if isinstance(ticket.get("policy"), dict) else {}
    body = "\n".join((
        "## Prepende support repair candidate",
        "",
        f"- Ticket: `{ticket.get('id')}`",
        f"- Tenant scope: `{ticket.get('scope')}`",
        f"- Category: `{policy.get('category', 'unknown')}`",
        f"- Reported page: `{page_url or 'not supplied'}`",
        "- Customer identity and credentials: redacted before dispatch",
        "",
        "## Sanitized report",
        "",
        description or "No usable description was supplied.",
        "",
        "## Fixed authority",
        "",
        "Reproduce first. Make the smallest reversible fix. Do not change billing, auth, secrets, DNS, legal text, tenant isolation, migrations, dependencies, or workflow files. Do not deploy. Add or update a regression test and leave a verification receipt in the pull request.",
    ))
    return {
        "title": f"[prepende-autofix] {title}",
        "body": body,
        "labels": ["prepende-autofix", "support-repair"],
    }
