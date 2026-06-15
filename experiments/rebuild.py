#!/usr/bin/env python3
"""Rebuild the data-driven parts of the public site from source.

Run weekly by .github/workflows/rebuild.yml (and runnable locally). It does two things:

  1. Smoke-checks every experiment sim still runs, and records the result + a UTC
     timestamp to experiments/data/_status.json. (The sims are ~deterministic, so this
     is a "the code still works" check, not a source of new numbers.)

  2. If experiments/predictions.jsonl exists, loads it through the prepende ledger,
     recomputes Brier / skill / calibration, and injects the live reliability polyline
     and headline numbers into docs/index.html between the <!--LEDGER:rel--> markers,
     plus a freshness stamp between <!--LEDGER:stamp--> markers.

Dependency-light: only the sims need numpy. The ledger path uses pure stdlib via the
prepende package. The script commits nothing — the workflow commits any resulting diff.

Honesty notes baked in:
  * If there are no resolved predictions, the ledger region is LEFT UNTOUCHED — the
    script never invents a calibration curve.
  * The reliability chart is drawn from the ACTUAL populated bins, so a sparse ledger
    looks sparse. n is always shown.
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
    """Return (summary_dict, calibration_bins, n_resolved) or None if no usable ledger."""
    if not os.path.exists(LEDGER_PATH) or os.path.getsize(LEDGER_PATH) == 0:
        return None
    sys.path.insert(0, ROOT)
    try:
        from prepende import Ledger, summary, calibration_table
    except Exception as e:
        print("prepende package not importable: %s" % e)
        return None
    records = Ledger(LEDGER_PATH).records()
    n_resolved = sum(1 for _, r in records if r is not None)
    s = summary(records)
    if not s.get("n_prob"):
        return {"summary": s, "bins": [], "n_resolved": n_resolved}
    bins = [b for b in calibration_table(records, nbins=5) if b["n"]]
    return {"summary": s, "bins": bins, "n_resolved": n_resolved}


def reliability_fragment(led):
    """Build the inline SVG fragment (polyline + circles + headline) for the site."""
    s = led["summary"]
    bins = led["bins"]
    lines = []
    pts = sorted(bins, key=lambda b: b["mean_pred"])
    if len(pts) >= 2:
        poly = " ".join("%.0f,%.0f" % (_x(b["mean_pred"]), _y(b["observed"])) for b in pts)
        lines.append('<polyline points="%s" fill="none" stroke="#4a3f9e" stroke-width="2"/>' % poly)
    for b in pts:
        r = 4 + min(4, b["n"] - 1)
        lines.append('<circle cx="%.0f" cy="%.0f" r="%d" fill="#4a3f9e"/>' % (
            _x(b["mean_pred"]), _y(b["observed"]), r))
    brier = s.get("brier")
    skill = s.get("skill")
    if brier is not None and skill is not None:
        lines.append('<text x="48" y="28" font-size="10" fill="#16233a">Brier %.2f · skill %+.2f</text>'
                     % (brier, skill))
    return "\n      ".join(lines)


def stamp_text(led, status):
    s = led["summary"]
    if s.get("n_prob"):
        return ("ledger: %d resolved · Brier %.2f · skill %+.2f · auto-rebuilt %s (UTC)"
                % (led["n_resolved"], s["brier"], s["skill"], status["rebuilt_utc"]))
    return ("ledger: %d locked, %d resolved · auto-rebuilt %s (UTC)"
            % (status.get("n_locked", 0), led["n_resolved"], status["rebuilt_utc"]))


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
        if led["bins"]:
            html, c1 = inject(html, "LEDGER:rel", reliability_fragment(led))
            changed = changed or c1
        html, c2 = inject(html, "LEDGER:stamp", stamp_text(led, status))
        changed = changed or c2
        if changed:
            with open(INDEX, "w", encoding="utf-8") as f:
                f.write(html)
            print("index.html ledger region refreshed")
    else:
        print("no usable predictions.jsonl yet - ledger region left untouched (honest no-op)")

    os.makedirs(os.path.dirname(STATUS), exist_ok=True)
    with open(STATUS, "w", encoding="utf-8") as f:
        json.dump(status, f, indent=2, sort_keys=True)
    print("status written: %s" % status["sims"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
