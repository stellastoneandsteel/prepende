#!/usr/bin/env python3
"""Rebuild the data-driven parts of the public site from source.

Run weekly by .github/workflows/rebuild.yml (and runnable locally). It does two things:

  1. Smoke-checks every experiment sim still runs, and records the result + a UTC
     timestamp to experiments/data/_status.json. (The sims are ~deterministic, so this
     is a "the code still works" check, not a source of new numbers.)

  2. Loads the immutable v1 corpus with explicit legacy limits. It publishes counts and
     an insufficient-evidence state. It does not publish calibration or skill below the
     n>=30 floor, pool retrospective rows, or label v1 as externally anchored.

Dependency-light: only the sims need numpy. The ledger path uses pure stdlib via the
prepende package. The script commits nothing — the workflow commits any resulting diff.

Honesty notes baked in:
  * The v1 ledger has unprotected resolution rows and is always labeled UNANCHORED.
  * Retrospective development rows are counted separately from forward rows.
  * The stale curve is actively removed whenever the evidence floor is not met.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXP = os.path.join(ROOT, "experiments")
DOCS = os.path.join(ROOT, "docs")
LEDGER_PATH = os.path.join(EXP, "predictions.jsonl")
V2_LEDGER_PATH = os.path.join(EXP, "predictions-v2.jsonl")
TRUSTED_ANCHORS_PATH = os.path.join(EXP, "trusted-anchor-keys.json")
TRUSTED_RESOLVERS_PATH = os.path.join(EXP, "trusted-resolver-keys.json")
EXTERNAL_ANCHOR_RECEIPTS_PATH = os.path.join(EXP, "external-anchor-receipts.json")
INDEX = os.path.join(DOCS, "index.html")
STATUS = os.path.join(EXP, "data", "_status.json")

SIMS = [
    "sim_oim_maxcut.py",
    "sim_oim_benchmark.py",
    "sim_telepathy_mock.py",
    "sim_telepathy_hard.py",
]

# inline reliability chart geometry (matches docs/index.html viewBox 0 0 270 220)
def _x(p):  # predicted probability 0..1 -> svg x
    return 40 + 210 * p

def _y(o):  # observed frequency 0..1 -> svg y
    return 184 - 170 * o


def _today():
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")


def smoke_sims():
    """Run each sim with a timeout; record pass/fail. Never fatal."""
    results = {}
    for name in SIMS:
        path = os.path.join(EXP, name)
        if not os.path.exists(path):
            results[name] = "missing"
            continue
        try:
            r = subprocess.run([sys.executable, path], cwd=EXP,
                               capture_output=True, text=True, timeout=300)
            results[name] = "ok" if r.returncode == 0 else "fail(rc=%d)" % r.returncode
        except subprocess.TimeoutExpired:
            results[name] = "timeout"
        except Exception as e:  # pragma: no cover - defensive
            results[name] = "error(%s)" % type(e).__name__
    return results


def load_ledger():
    """Return explicit legacy counts and integrity limits, never a pooled v1 curve."""
    if not os.path.exists(LEDGER_PATH) or os.path.getsize(LEDGER_PATH) == 0:
        return None
    sys.path.insert(0, ROOT)
    try:
        from prepende import Ledger, LegacyLedger, grouped_summaries
    except Exception as e:
        print("prepende package not importable: %s" % e)
        return None
    ledger = LegacyLedger(LEDGER_PATH)
    records = ledger.records()
    integrity = ledger.integrity()
    retrospective = [item for item in records if item[0].predictor == "prepende:dev-selftest"]
    forward = [item for item in records if item[0].predictor != "prepende:dev-selftest"]
    result = {
        "protocol": "prepende/1",
        "integrity": integrity,
        "n_locked": len(records),
        "n_resolved": sum(1 for _, terminal in records if terminal is not None),
        "n_pending": sum(1 for _, terminal in records if terminal is None),
        "n_retrospective": len(retrospective),
        "n_forward": len(forward),
        "n_forward_resolved": sum(1 for _, terminal in forward if terminal is not None),
        "bins": [],
        "evidence_status": "INSUFFICIENT_EVIDENCE",
    }
    if os.path.exists(V2_LEDGER_PATH):
        anchors = json.load(open(TRUSTED_ANCHORS_PATH, encoding="utf-8")) if os.path.exists(TRUSTED_ANCHORS_PATH) else {}
        resolvers = json.load(open(TRUSTED_RESOLVERS_PATH, encoding="utf-8")) if os.path.exists(TRUSTED_RESOLVERS_PATH) else {}
        receipts = json.load(open(EXTERNAL_ANCHOR_RECEIPTS_PATH, encoding="utf-8")) if os.path.exists(EXTERNAL_ANCHOR_RECEIPTS_PATH) else []
        v2 = Ledger(V2_LEDGER_PATH)
        verified = v2.verify(
            trusted_anchor_keys=anchors,
            trusted_resolver_keys=resolvers,
            external_anchor_receipts=receipts,
        )
        result["v2"] = verified.to_dict()
        result["v2_groups"] = grouped_summaries(v2.records())
        sufficient = [
            group for group in result["v2_groups"]
            if group["evidence_status"] == "SUFFICIENT" and group["provenance"] == "forward"
        ]
        result["curve_group"] = sufficient[0] if len(sufficient) == 1 else None
        result["curve_publishable"] = (
            verified.status == "OK"
            and verified.independently_resolved
            and result["curve_group"] is not None
        )
    else:
        result["v2"] = None
        result["v2_groups"] = []
        result["curve_publishable"] = False
    return result


def reliability_fragment(led):
    """Return a visible fail-closed state while the forward cohort is below n=30."""
    v2 = led.get("v2") or {}
    if led.get("curve_publishable"):
        group = led["curve_group"]
        bins = group["reliability"]["bins"]
        points = [item for item in bins if item["n"]]
        poly = " ".join("%.0f,%.0f" % (_x(item["mean_pred"]), _y(item["observed"])) for item in points)
        circles = [
            '<circle cx="%.0f" cy="%.0f" r="%d" fill="#4a3f9e"/>' % (
                _x(item["mean_pred"]), _y(item["observed"]), 4 + min(4, item["n"] - 1)
            ) for item in points
        ]
        return "\n      ".join([
            '<polyline points="%s" fill="none" stroke="#4a3f9e" stroke-width="2"/>' % poly,
            *circles,
            '<text x="48" y="28" font-size="10" fill="#16233a">verified forward, independently resolved v2 cohort n=%d</text>' % group["n_prob"],
        ])
    return (
        '<text x="145" y="92" text-anchor="middle" font-size="11" fill="#9a2d2d">'
        'INSUFFICIENT EVIDENCE</text>\n      '
        '<text x="145" y="112" text-anchor="middle" font-size="10" fill="#5b6675">'
        'forward resolved n=%d; curve requires n&gt;=30</text>\n      '
        '<text x="145" y="130" text-anchor="middle" font-size="10" fill="#5b6675">'
        'legacy v1 resolutions are not hash-protected; v2=%s</text>'
        % (led["n_forward_resolved"], v2.get("status", "absent"))
    )


def stamp_text(led, status):
    v2 = led.get("v2") or {}
    return (
        "legacy v1: %d locked, %d resolved, %d pending (%.1f%% unresolved); "
        "v1 has no locked forfeit state; %d retrospective excluded; "
        "UNANCHORED; v2=%s with %d contracts; calibration suppressed unless a segregated "
        "forward v2 cohort is OK, independently resolved, unique, and n>=30; auto-rebuilt %s (UTC)"
        % (led["n_locked"], led["n_resolved"], led["n_pending"],
           (100.0 * led["n_pending"] / led["n_locked"]) if led["n_locked"] else 0.0,
           led["n_retrospective"], v2.get("status", "absent"),
           (v2.get("counts") or {}).get("contracts", 0), status["rebuilt_utc"])
    )


def inject(html, marker, payload):
    """Replace content between <!--MARKER--> and <!--/MARKER--> with payload."""
    pat = re.compile(r"(<!--%s-->)(.*?)(<!--/%s-->)" % (re.escape(marker), re.escape(marker)), re.S)
    if not pat.search(html):
        print("marker %s not found in index.html (skipped)" % marker)
        return html, False
    new = pat.sub(lambda m: m.group(1) + "\n      " + payload + "\n      " + m.group(3), html)
    return new, new != html


def main():
    status = {"rebuilt_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
              "sims": smoke_sims()}
    led = load_ledger()

    changed = False
    if led is not None and os.path.exists(INDEX):
        with open(INDEX, encoding="utf-8") as f:
            html = f.read()
        html, c1 = inject(html, "LEDGER:rel", reliability_fragment(led))
        changed = changed or c1
        html, c2 = inject(html, "LEDGER:stamp", stamp_text(led, status))
        changed = changed or c2
        if changed:
            with open(INDEX, "w", encoding="utf-8") as f:
                f.write(html)
            print("index.html ledger region refreshed")
        status["ledger"] = {
            "protocol": led["protocol"],
            "status": led["integrity"]["status"],
            "locked": led["n_locked"],
            "resolved": led["n_resolved"],
            "pending": led["n_pending"],
            "retrospective": led["n_retrospective"],
            "curvePublished": bool(led.get("curve_publishable")),
            "v2Status": (led.get("v2") or {}).get("status", "absent"),
        }
    else:
        print("no usable predictions.jsonl yet - ledger region left untouched (honest no-op)")

    os.makedirs(os.path.dirname(STATUS), exist_ok=True)
    with open(STATUS, "w", encoding="utf-8") as f:
        json.dump(status, f, indent=2, sort_keys=True)
    print("status written: %s" % status["sims"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
