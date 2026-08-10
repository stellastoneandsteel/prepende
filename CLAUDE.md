# CLAUDE.md, prepende

Project context for every Claude Code session in this repo. Written 2026-08-08,
grounded in a full code audit (8-agent study + first-hand verification). If
something here looks wrong, open the file and trust what you see.

The project name in conversation is **/prepende**.

---

## 1. What this repo is

**Verifiable proof that a predictor's confidence is trustworthy** — that is the
target claim. What the code delivers today is honest-operator bookkeeping; the
gap between the two is the roadmap (section 5).

Two parts held to one standard:

- `prepende/` — the calibration tool. Pure standard library, no dependencies,
  MIT. ~719 lines across 11 modules. A Contract (predictor, question, kind,
  claim, resolution_rule, eval_regime, created_at) is canonically JSON-serialized
  and SHA-256 hashed at lock time; resolutions must reference the contract hash
  and exactly match the locked eval_regime or `RetrofitError` is raised. Metrics:
  Brier, log loss, skill vs base rate, ECE/MCE, Murphy decomposition, per-bin
  Wilson 95% CIs, dependency-free SVG reliability diagram. `resolvability.py` is
  a second, orthogonal axis: can a third party even check this claim?
- `experiments/` — the research program that uses the tool on itself.
  Pre-registered batches (numpy/ngspice sims, an evidence study), resolved
  honestly including public MISSES. Ledger: `experiments/predictions.jsonl`.
- `docs/` — the GitHub Pages site (Settings -> Pages -> /docs) with the logbook
  and live calibration numbers, rebuilt by a weekly GitHub Action.

GitHub: `stellastoneandsteel/prepende`. Owner: Ryan Amerio.

**Name collision, do not conflate:** the production "Prepende" inside
Stella/Dealroom (`stella-website/netlify/functions/prepende-mcp.mjs`, backed by
the Engram substrate at `~/Development/engram`) is a knowledge/memory MCP brain
with zero prediction machinery. Different product, shared name. Never cite it as
production use of this ledger.

## 2. Commands

Run from repo root (not pip-installable yet; `pyproject.toml` is empty):

```bash
python3 -m prepende.tests    # test suite
python3 -m prepende.demo     # report + reliability SVG + anti-retrofit proofs
python3 -m prepende --ledger experiments/predictions.jsonl report
```

## 3. Ground-truth state (verified 2026-08-08)

- Ledger: 26 contracts, 14 resolutions, 12 unresolved. Predictors: 22
  `prepende`, 4 `prepende:dev-selftest` (retrospective; must never be pooled
  unlabeled into forward aggregates).
- Site headline (Brier 0.19, skill +0.21, 14 resolved) is below the README's own
  n>=30 validity floor. Known issue, not a talking point.
- The genuinely novel mechanism, confirmed by prior-art sweep: hashing claim +
  resolution rule + eval regime together and refusing resolution under a changed
  regime. Nobody else ships this combination. Closest: Foresight Arena
  (on-chain agent forecasts), Prep-eval (no crypto layer).

## 4. Known gaps (all verified in code; fixing these is the point)

1. `Ledger.integrity()` re-hashes only contract rows. Resolution rows are
   unprotected and rows can be deleted undetected: no hash chain, sequence
   numbers, or row-count commitment (ledger.py:81-89).
2. `created_at` is caller-supplied and hashed in, so backdating self-verifies
   (contract.py, `lock_prediction`). No OpenTimestamps/RFC3161 anywhere in code.
3. Self-resolution: the same party locks and resolves; `resolution_rule` is
   prose, never executed; the regime gate is string equality after strip.
4. No completeness commitment: unresolved predictions carry no penalty, dup
   locks inflate n, p outside [0,1] flows into scoring unvalidated.
5. O(n^2) ledger reads, no file locking, and a separated protocol-vs-brain verification surface:
   root Protocol v2 suites now run via `python -m unittest discover -s tests -v` and
   brain runtime behavior runs through `scripts/verify_prepende_brain.py`; `test_protocol_v2.py`
   and this file's smoke gate intentionally remain independent proof boundaries.

**Status update, 2026-08-08 (later same day):** branch `codex/prepende-protocol-v2`
carries a large work-in-progress (Codex-authored) that implements much of section
5 item 1 already: `docs/PROTOCOL_V2.md` (protocol id `prepende/2`) specifies
hash-chained rows with order/content commitments, signed anchored checkpoints,
a `complete_through` completeness claim, independent resolver signatures, and
canonical NFC/sorted-key encoding; new modules `anchors.py`, `signing.py`,
`canonical.py`, `evaluators.py`, `legacy.py`, plus schemas, tests, and CI.
Section 4 describes the v1 code; verify against the current branch before
treating those gaps as open. Do not duplicate v2 work; coordinate with the
Codex branch.

## 5. Roadmap (priority order)

1. **Harden the ledger so the claim becomes true.** Hash-chain every row
   including resolutions; anchor every lock hash externally (OpenTimestamps or a
   free RFC3161 TSA) at creation; reject caller-supplied `created_at`; make
   unresolved-rate and resolution deadlines first-class with forfeit scoring;
   executable resolution rules over hash-pinned data sources; signed third-party
   resolver identities; input validation; duplicate-cid rejection; real
   pyproject + PyPI release.
2. **Publish the protocol spec.** 2 pages, versioned: commitment format,
   canonical serialization, regime-lock semantics, anchoring requirements,
   completeness reporting. Strengthen regime binding from prose string-equality
   toward hashing the evaluation artifact itself (harness code hash, dataset
   hash, container digest). The spec and the corpus are ownable; 719 lines of
   stdlib is not.
3. **Wire the ledger into the production agent path.** Stella/Mimi agent
   commitments (quotes, delivery dates, conversion estimates) hash-locked at
   issuance, resolved against real business outcomes, agent autonomy gated on
   demonstrated calibration. The accumulated externally-anchored corpus is the
   only permanent moat: competitors cannot backfill history.
4. **Split the names** (or wire the real ledger into the Stella brain) so
   neither product borrows the other's credibility.

## 6. Rules

- **The ledger is append-only.** Never edit or delete an existing row, even to
  fix a mistake. Append a correction and note it.
- **Honesty floor is structural.** Misses get headlined. No aggregate below the
  stated n floor. Retrospective and self-resolved rows are labeled in every
  published number. Claims are tagged testable vs posture.
- **Never overclaim tamper-evidence.** State exactly what `integrity()` covers
  and no more. The current README overstates it; fix it, do not repeat it.
- **Lock before you look.** Any new experiment locks its contracts and commits
  before the sim runs. Prefer lock-to-resolve windows long enough that git
  ordering means something.
- No hardcoded keys, no committed secrets, no em dashes in copy.
