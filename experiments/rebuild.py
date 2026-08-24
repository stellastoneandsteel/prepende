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
import html as _html
import json
import os
import re
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from prepende.report import MIN_CALIBRATION_N

EXP = os.path.join(ROOT, "experiments")
DOCS = os.path.join(ROOT, "docs")
LEDGER_PATH = os.path.join(EXP, "predictions.jsonl")
V2_LEDGER_PATH = os.path.join(EXP, "predictions-v2.jsonl")
TRUSTED_ANCHORS_PATH = os.path.join(EXP, "trusted-anchor-keys.json")
TRUSTED_RESOLVERS_PATH = os.path.join(EXP, "trusted-resolver-keys.json")
EXTERNAL_ANCHOR_RECEIPTS_PATH = os.path.join(EXP, "external-anchor-receipts.json")
INDEX = os.path.join(DOCS, "index.html")
STATUS = os.path.join(EXP, "data", "_status.json")

_NUMBER = re.compile(r"(?<![A-Za-z0-9])(?:\d+(?:\.\d+)?|\.\d+)%?")
_CALIBRATION_METRIC = re.compile(r"\b(?:brier(?:\s+score)?|log[ _-]?loss|ece|mce)\b", re.I)
_CALIBRATION_METRIC_VALUE = re.compile(
    r"(?:\b(?:brier(?:\s+score)?|log[ _-]?loss|ece|mce)\b[^\n.!?]{0,50}"
    r"(?<![A-Za-z0-9])(?:\d+(?:\.\d+)?|\.\d+)%?"
    r"|(?<![A-Za-z0-9])(?:\d+(?:\.\d+)?|\.\d+)%?[^\n.!?]{0,50}"
    r"\b(?:brier(?:\s+score)?|log[ _-]?loss|ece|mce)\b)",
    re.I,
)
_NON_RESULT_METRIC_CONTEXT = re.compile(
    r"\b(?:confidence|diagram|dimension|maximum|minimum|penalty|range|scale|"
    r"threshold|version|wilson)\b",
    re.I,
)
_SKILL_METRIC = re.compile(r"\bskill(?:\s+score)?\b", re.I)
_CALIBRATION_CONTEXT = re.compile(r"\b(?:calibrat\w*|forecast\w*|prediction\w*)\b", re.I)
_FORECAST_RESULT = re.compile(
    r"(?:\b\d+\s*(?:/|of)\s*\d+\b|\b\d+\s+hits?\b|\b\d+\s+miss(?:es|ed)?\b)"
    r"[^\n.!?]{0,120}\b(?:calibrat\w*|hit[ _-]+rate|accuracy|benchmark|forward[ _-]+record)\b"
    r"|\b(?:calibrat\w*|hit[ _-]+rate|accuracy|benchmark|forward[ _-]+record)\b[^\n.!?]{0,120}"
    r"(?:\b\d+\s*(?:/|of)\s*\d+\b|\b\d+\s+hits?\b|\b\d+\s+miss(?:es|ed)?\b)",
    re.I,
)
_FORECAST_OUTCOME = re.compile(
    r"(?:\b(?:right|correct)\b[^\n.!?]{0,30}\b\d+\s+(?:/|of)\s*\d+\b"
    r"[^\n.!?]{0,40}\b(?:predictions?|forecasts?)\b"
    r"|\b\d+\s+(?:/|of)\s*\d+\b[^\n.!?]{0,40}"
    r"\b(?:predictions?|forecasts?)\b[^\n.!?]{0,30}\b(?:right|correct)\b)",
    re.I,
)
_ESTABLISHED_BENCHMARK = re.compile(
    r"(?:\b(?:established|validated|proven|definitive)\b[^\n.!?]{0,80}"
    r"\b(?:(?:calibrat\w*|forecast\w*|prediction\w*)\s+)?(?:benchmark|record|result|score)\b"
    r"|\b(?:(?:calibrat\w*|forecast\w*|prediction\w*)\s+)?(?:benchmark|record|result|score)\b"
    r"[^\n.!?]{0,40}\b(?:established|validated|proven|definitive)\b)",
    re.I,
)
_SETS_BENCHMARK = re.compile(
    r"\b(?:establish(?:es|ed|ing)?|set(?:s|ting)?)\b[^\n.!?]{0,30}\bbenchmark\b",
    re.I,
)
_NEGATED_BENCHMARK = re.compile(
    r"(?:\b(?:not|never|no)\b[^\n.!?]{0,30}"
    r"\b(?:an?\s+)?(?:established|validated|proven|definitive)?\s*"
    r"(?:(?:calibrat\w*|forecast\w*|prediction\w*)\s+)?(?:benchmark|record|result|score)\b"
    r"|\b(?:(?:calibrat\w*|forecast\w*|prediction\w*)\s+)?(?:benchmark|record|result|score)\b"
    r"[^\n.!?]{0,30}\b(?:not|never|no)\b[^\n.!?]{0,20}"
    r"\b(?:established|validated|proven|definitive)\b)",
    re.I,
)
_CAVEAT = re.compile(
    r"\b(?:preliminary|exploratory|insufficient(?:[ _-]+evidence)?|below(?:[ _-]+the)?"
    r"[ _-]+(?:sample[ _-]+)?floor|below[ _-]+threshold|suppressed|not[ _-]+(?:a[ _-]+)?"
    r"calibration[ _-]+(?:claim|evidence|benchmark)|not[ _-]+(?:an?[ _-]+)?"
    r"(?:(?:established|validated|proven|definitive)[ _-]+)?"
    r"(?:(?:calibration|forecast|prediction)[ _-]+)?(?:benchmark|record|result|score))\b|"
    r"\brequires\s+n\s*>=|"
    r"\bbelow\b[^\n.!?;—–]{0,50}\b(?:floor|threshold)\b",
    re.I,
)
_SAMPLE_PATTERNS = (
    re.compile(r"\bn\s*(?:=|:|of)\s*(\d+)\b", re.I),
    re.compile(r"\b(\d+)\s+(?:resolved|predictions?|forecasts?)\b", re.I),
    re.compile(r"\bsample[ _-]*size\s*(?:=|:|of)\s*(\d+)\b", re.I),
    re.compile(r"\b(\d+)\s+(?:observations?|outcomes?|cases?)\b", re.I),
)
_HIT_MISS = re.compile(r"\b(\d+)\s+hits?\s*/\s*(\d+)\s+miss(?:es)?\b", re.I)
_SENTENCE_BOUNDARY = re.compile(r"(?<=[!?])\s+|(?<!\d)\.(?!\d)(?:\s+|$)")
_CLAUSE_BOUNDARY = re.compile(r"\s*(?:;|—|–)\s*")
_POLICY_COUNT = re.compile(
    r"\b(?:floor|minimum|policy|require[ds]?|threshold)\b",
    re.I,
)

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


class _PublishingHTMLParser(HTMLParser):
    """Collect visible/metadata claim units while keeping table rows together."""

    _BOUNDARIES = {
        "article",
        "div",
        "figcaption",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "li",
        "p",
        "section",
        "text",
        "tr",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[str] = []
        self._parts: list[str] = []
        self._ignored_depth = 0
        self._json_ld_depth = 0

    def _flush(self) -> None:
        block = " ".join(" ".join(self._parts).split())
        if block:
            self.blocks.append(block)
        self._parts = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        attributes = {key.lower(): value for key, value in attrs}
        if lowered == "meta":
            content = attributes.get("content")
            if content:
                self._flush()
                self.blocks.append(" ".join(_html.unescape(content).split()))
            return
        for attribute in ("alt", "aria-label", "title"):
            value = attributes.get(attribute)
            if value:
                self._flush()
                self.blocks.append(" ".join(_html.unescape(value).split()))
        if lowered == "script":
            self._flush()
            if str(attributes.get("type") or "").lower() == "application/ld+json":
                self._json_ld_depth += 1
            else:
                self._ignored_depth += 1
            return
        if lowered == "style":
            self._flush()
            self._ignored_depth += 1
            return
        if self._ignored_depth == 0 and lowered in self._BOUNDARIES:
            self._flush()

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered == "script":
            if self._json_ld_depth:
                self._json_ld_depth -= 1
                self._flush()
            elif self._ignored_depth:
                self._ignored_depth -= 1
            return
        if lowered == "style":
            if self._ignored_depth:
                self._ignored_depth -= 1
            return
        if self._ignored_depth == 0 and lowered in self._BOUNDARIES:
            self._flush()

    def handle_data(self, data: str) -> None:
        if self._ignored_depth == 0:
            self._parts.append(data)

    def close(self) -> None:
        super().close()
        self._flush()


def _claim_blocks(path: str, content: str) -> list[str]:
    """Return independently caveated publishing blocks from Markdown or HTML."""

    if path.endswith((".html", ".htm", ".svg")):
        parser = _PublishingHTMLParser()
        parser.feed(content)
        parser.close()
        return parser.blocks
    if path.endswith(".json"):
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            return [" ".join(content.split())]
        records: list[str] = []

        def collect(value: object) -> None:
            if isinstance(value, dict):
                scalars = []
                for key, item in value.items():
                    if isinstance(item, (str, int, float, bool)) or item is None:
                        scalars.append(f"{key}: {item}")
                    else:
                        collect(item)
                text = " ".join(" ".join(scalars).split())
                if text:
                    records.append(text)
            elif isinstance(value, str):
                text = " ".join(value.split())
                if text:
                    records.append(text)
            elif isinstance(value, list):
                for item in value:
                    collect(item)

        collect(payload)
        return records
    # Fenced examples remain public text and cannot hide a performance claim.
    prose = re.sub(r"^\s*(?:```|~~~).*?$", " ", content, flags=re.M)
    return [" ".join(block.split()) for block in re.split(r"\n\s*\n", prose) if block.strip()]


def _claim_sentences(block: str) -> list[str]:
    sentences: list[str] = []
    for sentence in _SENTENCE_BOUNDARY.split(block):
        sentences.extend(
            " ".join(clause.split())
            for clause in _CLAUSE_BOUNDARY.split(sentence)
            if clause.strip()
        )
    return sentences


def _sample_counts(block: str) -> list[int]:
    counts = []
    for pattern in _SAMPLE_PATTERNS:
        for match in pattern.finditer(block):
            context = block[max(0, match.start() - 45): min(len(block), match.end() + 45)]
            if _POLICY_COUNT.search(context):
                continue
            counts.append(int(match.group(1)))
    counts.extend(int(match.group(1)) + int(match.group(2)) for match in _HIT_MISS.finditer(block))
    return counts


def _without_sample_markers(sentence: str) -> str:
    result = sentence
    for pattern in _SAMPLE_PATTERNS:
        result = pattern.sub(" ", result)
    return result


def _established_benchmark(sentence: str) -> bool:
    asserted = bool(
        _ESTABLISHED_BENCHMARK.search(sentence) or _SETS_BENCHMARK.search(sentence)
    )
    performance_context = bool(
        _CALIBRATION_CONTEXT.search(sentence)
        or _CALIBRATION_METRIC.search(sentence)
        or _FORECAST_OUTCOME.search(sentence)
        or _FORECAST_RESULT.search(sentence)
    )
    return asserted and performance_context and not bool(
        _NEGATED_BENCHMARK.search(sentence)
    )


def _quantitative_calibration_claim(sentence: str) -> bool:
    without_samples = _without_sample_markers(sentence)
    has_number = bool(_NUMBER.search(without_samples))
    named_metric = any(
        not _NON_RESULT_METRIC_CONTEXT.search(match.group(0))
        for match in _CALIBRATION_METRIC_VALUE.finditer(without_samples)
    )
    skill_metric = bool(
        _SKILL_METRIC.search(without_samples)
        and _CALIBRATION_CONTEXT.search(without_samples)
        and has_number
    )
    forecast_metric = bool(
        _CALIBRATION_CONTEXT.search(without_samples)
        and re.search(
            r"\b(?:accuracy|benchmark|hit[ _-]+rate|forward[ _-]+record|right|"
            r"score|success[ _-]+rate|correct)\b",
            without_samples,
            re.I,
        )
        and has_number
    )
    return bool(
        named_metric
        or skill_metric
        or forecast_metric
        or _FORECAST_RESULT.search(sentence)
        or _FORECAST_OUTCOME.search(sentence)
        or _established_benchmark(sentence)
    )


def audit_public_calibration_claims(
    documents: dict[str, str],
    *,
    curve_publishable: bool,
    minimum_n: int = MIN_CALIBRATION_N,
) -> list[dict[str, object]]:
    """Reject below-floor calibration performance claims without a local caveat.

    The n>=30 floor remains owned by ``prepende.report``.  Caveats must be in the
    same publishing block, so moving a warning elsewhere cannot bless a claim.
    """

    floor = max(MIN_CALIBRATION_N, int(minimum_n))
    violations: list[dict[str, object]] = []
    for path, content in sorted(documents.items()):
        for block_index, block in enumerate(_claim_blocks(path, content), start=1):
            for sentence_index, sentence in enumerate(_claim_sentences(block), start=1):
                if not _quantitative_calibration_claim(sentence):
                    continue
                counts = _sample_counts(sentence)
                below_floor = any(count < floor for count in counts)
                sample_authority_missing = not counts
                evidence_insufficient = (
                    below_floor or sample_authority_missing or not curve_publishable
                )
                established = _established_benchmark(sentence)
                caveated = bool(_CAVEAT.search(sentence))
                if evidence_insufficient and (established or not caveated):
                    if established:
                        reason = "below-floor-established-benchmark"
                    elif sample_authority_missing:
                        reason = "claim-without-sample-authority"
                    elif below_floor:
                        reason = "below-floor-claim-without-local-caveat"
                    else:
                        reason = "unpublishable-claim-without-local-caveat"
                    violations.append(
                        {
                            "path": path,
                            "block": block_index,
                            "sentence": sentence_index,
                            "reason": reason,
                            "sampleCounts": counts,
                            "excerpt": sentence[:240],
                        }
                    )
    return violations


def public_claim_documents(root: str | Path = ROOT) -> dict[str, str]:
    base = Path(root)
    paths = sorted(base.glob("*.md"))
    for extension in ("*.md", "*.html", "*.htm", "*.svg", "*.json"):
        paths.extend(sorted((base / "docs").rglob(extension)))
    return {
        path.relative_to(base).as_posix(): path.read_text(encoding="utf-8")
        for path in paths
        if path.is_file()
    }


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
        'forward resolved n=%d; curve requires n&gt;=%d</text>\n      '
        '<text x="145" y="130" text-anchor="middle" font-size="10" fill="#5b6675">'
        'legacy v1 resolutions are not hash-protected; v2=%s</text>'
        % (led["n_forward_resolved"], MIN_CALIBRATION_N, v2.get("status", "absent"))
    )


def stamp_text(led, status):
    v2 = led.get("v2") or {}
    return (
        "legacy v1: %d locked, %d resolved, %d pending (%.1f%% unresolved); "
        "v1 has no locked forfeit state; %d retrospective excluded; "
        "UNANCHORED; v2=%s with %d contracts; calibration suppressed unless a segregated "
        "forward v2 cohort is OK, independently resolved, unique, and n>=%d; auto-rebuilt %s (UTC)"
        % (led["n_locked"], led["n_resolved"], led["n_pending"],
           (100.0 * led["n_pending"] / led["n_locked"]) if led["n_locked"] else 0.0,
           led["n_retrospective"], v2.get("status", "absent"),
           (v2.get("counts") or {}).get("contracts", 0), MIN_CALIBRATION_N,
           status["rebuilt_utc"])
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
        documents = public_claim_documents(ROOT)
        documents[Path(INDEX).relative_to(ROOT).as_posix()] = html
        violations = audit_public_calibration_claims(
            documents,
            curve_publishable=bool(led.get("curve_publishable")),
        )
        if violations:
            print("public calibration claim gate failed: %s" % json.dumps(violations, sort_keys=True))
            return 1
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
