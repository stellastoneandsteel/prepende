"""Memory eval — the gate that autonomy answers to.

Doctrine (owner, 2026-07-04): no autonomy without a tight evaluation loop.
Before the brain is allowed to keep more of its own memory, something must
measure whether kept memory actually makes answers BETTER. This is that
something.

Method — controlled A/B over the same answer path chat uses:
  scope A (baseline):   known facts only
  scope B (treatment):  known facts + the candidate memories under test
For each probe question, recall k memories from the scope and answer with the
same injection-guarded memory preamble the solo tactic uses, then score the
answer by containment of the probe's expected strings. The candidates' worth
is the score lift B - A; per-candidate attribution runs leave-one-in when the
candidate set is small.

Provider note (deliberate): under MODEL_PROVIDER=echo the reply echoes the
recalled notes verbatim, so the eval isolates the RECALL layer — "does hybrid
search surface the right memory for this question" — deterministically and at
zero cost. Under a real model it evaluates recall + generation together. Both
are honest; they measure different layers, and the report says which ran.

Run:  python3 -m self_improve.memory_eval --probes probes.json \
        [--baseline baseline.json] [--candidates candidates.json]
Probe shape:     [{"question": str, "must_contain": [str, ...]}, ...]
Fact-file shape: ["fact one", "fact two", ...]
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from typing import Any, Sequence

from tactics._context import memory_preamble

# Attribution is leave-one-in (one probe-set pass per candidate); keep the
# bound low so a big candidate batch can't turn the eval into a model-bill.
MAX_ATTRIBUTED_CANDIDATES = 8


def _score(answer: str, must_contain: Sequence[str]) -> float:
    """Deterministic containment: fraction of expected strings present."""
    if not must_contain:
        return 0.0
    low = (answer or "").lower()
    return sum(1 for m in must_contain if m.lower() in low) / len(must_contain)


async def _answer(gateway: Any, store: Any, scope: str, question: str, k: int = 5) -> str:
    recalled = list(await store.search(question, scope=scope, k=k))
    notes = memory_preamble(recalled)
    messages = [{"role": "user", "content": f"{notes}\n\n{question}" if notes else question}]
    return str(await gateway.complete(messages))


async def _condition_score(gateway: Any, store: Any, scope: str,
                           probes: Sequence[dict], k: int) -> tuple[float, list[dict]]:
    per_probe = []
    for p in probes:
        ans = await _answer(gateway, store, scope, str(p["question"]), k=k)
        s = _score(ans, list(p.get("must_contain", [])))
        per_probe.append({"question": p["question"], "score": s})
    mean = sum(pp["score"] for pp in per_probe) / max(1, len(per_probe))
    return mean, per_probe


async def evaluate_memory_lift(gateway: Any, probes: Sequence[dict],
                               baseline_facts: Sequence[str],
                               candidate_facts: Sequence[str],
                               *, k: int = 5) -> dict[str, Any]:
    """A/B the probe set over baseline vs baseline+candidates. Pure and
    self-contained: temp sqlite store, throwaway scopes, no durable writes."""
    from memory.sqlite_store import SqliteMemoryStore
    with tempfile.TemporaryDirectory() as td:
        store = SqliteMemoryStore(os.path.join(td, "eval.db"))

        async def seed(scope: str, facts: Sequence[str]) -> None:
            for f in facts:
                await store.write(str(f), scope=scope, metadata={"source": "memory_eval"})

        await seed("eval-a", baseline_facts)
        await seed("eval-b", list(baseline_facts) + list(candidate_facts))
        base_mean, base_probes = await _condition_score(gateway, store, "eval-a", probes, k)
        with_mean, with_probes = await _condition_score(gateway, store, "eval-b", probes, k)

        per_candidate: list[dict] = []
        if 0 < len(candidate_facts) <= MAX_ATTRIBUTED_CANDIDATES:
            for i, cand in enumerate(candidate_facts):
                scope = f"eval-c{i}"
                await seed(scope, list(baseline_facts) + [cand])
                mean_i, _ = await _condition_score(gateway, store, scope, probes, k)
                per_candidate.append({"content": str(cand)[:200],
                                      "lift": round(mean_i - base_mean, 4)})

        return {
            "provider": getattr(gateway, "name", "?"),
            "layerMeasured": "recall" if getattr(gateway, "name", "") == "echo" else "recall+generation",
            "probes": len(probes),
            "baselineScore": round(base_mean, 4),
            "withCandidatesScore": round(with_mean, 4),
            "lift": round(with_mean - base_mean, 4),
            "perProbeBaseline": base_probes,
            "perProbeWith": with_probes,
            "perCandidate": per_candidate,
            "verdict": ("helps" if with_mean > base_mean
                        else "no_effect" if with_mean == base_mean else "hurts"),
        }


def _load(path: str) -> Any:
    with open(path) as fh:
        return json.load(fh)


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="A/B eval: does candidate memory improve answers?")
    ap.add_argument("--probes", required=True, help="JSON: [{question, must_contain:[...]}]")
    ap.add_argument("--baseline", default=None, help="JSON list of baseline facts")
    ap.add_argument("--candidates", default=None, help="JSON list of candidate facts")
    ap.add_argument("--k", type=int, default=5)
    args = ap.parse_args()

    from kernel.core.config import Config
    from models.factory import build_gateway
    gateway = build_gateway(Config())
    report = asyncio.run(evaluate_memory_lift(
        gateway,
        _load(args.probes),
        _load(args.baseline) if args.baseline else [],
        _load(args.candidates) if args.candidates else [],
        k=args.k,
    ))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
