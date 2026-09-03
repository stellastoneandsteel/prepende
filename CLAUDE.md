# CLAUDE.md, prepende

Project context for every Claude Code session in this repo. Written 2026-09-03,
grounded in a full code audit and verification against origin/main. If something
here looks wrong, open the file and trust what you see.

The project name in conversation is **/prepende**. Stella-org work, not Mimi.

---

## 1. What this repo is

Verifiable proof that a predictor's confidence is trustworthy is the target
claim. What the code delivers today is an internally chained, deterministic
protocol release candidate (prepende/2) with explicit external trust boundaries.
The gap between current bookkeeping and external anchoring is the unpaid roadmap
(section 5).

Four distinct parts held to one standard:

- `prepende/`: The prediction commitment protocol package (`prepende-protocol`
  0.3.0rc1, requires-python >=3.10). ~2,884 lines across 16 modules. Pure
  standard library for core canonicalization, chaining, evaluators, and scoring;
  optional extras `signatures` (cryptography >=45 for Ed25519 anchors and
  resolvers) and `test` (build >=1.2, jsonschema >=4.23). Console script
  `prepende`. GitHub Releases is the only supported distribution channel; it is
  not on PyPI (see `docs/PUBLISHING.md`). Commitments are sequence-numbered,
  canonical-JSON serialized, and SHA-256 hash-chained; resolutions require
  pinned evidence digests and evaluate against locked deterministic specs;
  non-resolution carries explicit forfeit policies; external trust uses signed
  checkpoint anchors and independent resolver keys.
- `experiments/`: Pre-registered research simulations and prediction ledgers.
  Contains the sealed v1 legacy ledger (`experiments/predictions.jsonl`), the
  live v2 hash-chained stream (`experiments/predictions-v2.jsonl`), experiment
  specs in `experiments/specs/`, and simulation scripts.
- `docs/`: The GitHub Pages site (`docs/index.html`) with the project logbook
  and public calibration state, rebuilt by a weekly GitHub Action. Also houses
  formal protocol specifications (`docs/PROTOCOL_V2.md`), threat model
  (`docs/THREAT_MODEL_V2.md`), publishing policy (`docs/PUBLISHING.md`), anchor
  spike documentation (`docs/ANCHOR_PROVIDER_SPIKE_V2.md`), forfeit postmortem
  (`docs/V2_FORFEIT_POSTMORTEM_2026-08-23.md`), and production pilot gates
  (`docs/PRODUCTION_PILOT_V2.md`).
- `brain/`: A separate subsystem housing the product-neutral Prepende Brain
  runtime (knowledge graph, lexical RAG, memory candidate pipeline, MCP stdio
  and HTTP servers). Run with `./brain/bin/prepende`. Verified through its own
  independent gate: `.venv/bin/python scripts/verify_prepende_brain.py` run from
  `brain/` (passed=50, discovered=51, executable=50, with `smoke_clone_privacy.py`
  as a reviewed public-core exclusion). Do not conflate the brain runtime with
  the prediction protocol.

GitHub: `stellastoneandsteel/prepende`. Owner: Ryan Amerio.

**Name collision warning, do not conflate:**
The production "Prepende" inside Stella/Dealroom
(`stella-website/netlify/functions/prepende-mcp.mjs`, backed by the Engram
substrate at `~/Development/engram`) is a knowledge and memory MCP brain with
zero prediction machinery. Different product, shared name. Never cite it as
production use of this prediction protocol or ledger.

## 2. Commands that actually work on main

Run from repo root:

```bash
# Installation (editable development install)
python3 -m pip install -e .
# Install with optional Ed25519 signatures and test extras:
python3 -m pip install -e '.[signatures,test]'

# Test suites
python3 -m prepende.tests                  # Root test suite (76 tests; 33 skip without extras, 76 pass with extras)
python3 -m unittest discover -s tests -v   # Unittest discovery test runner
python3 -m prepende.demo                   # Demo report + reliability SVG + anti-retrofit proofs

# Protocol CLI and ledger verification
python3 -m prepende --help                 # Protocol CLI usage and commands
python3 -m prepende --ledger experiments/predictions-v2.jsonl verify         # Verify v2 hash chain & anchor status
python3 -m prepende --ledger experiments/predictions-v2.jsonl report         # Full v2 audit report
python3 -m prepende --ledger experiments/predictions.jsonl legacy-report      # Inspect v1 corpus with legacy limits

# Site rebuild and simulations
python3 experiments/rebuild.py             # Re-runs experiment sims and updates data-driven site files (requires numpy)

# Brain subsystem (run from brain/ directory)
.venv/bin/python scripts/verify_prepende_brain.py  # Brain verification gate (passed=50)
./bin/prepende status                             # Brain CLI status check
```

## 3. Ground-truth state (verified 2026-09-03)

### Protocol package
- Package version: `prepende-protocol 0.3.0rc1`, `requires-python >=3.10`.
- Optional extras: `signatures` (`cryptography>=45`) and `test` (`build>=1.2`, `jsonschema>=4.23`).
- Console script entrypoint: `prepende` (`prepende.__main__:main`).
- Distribution: GitHub Releases only (`docs/PUBLISHING.md`). Not published to PyPI.
- Root test suite has 76 tests. Locally, 33 tests skip without the `signatures` extra. Never report a local run as 76 passed unless extras were installed. CI executes `.[signatures,test]` on Python 3.10 through 3.13.

### v1 ledger (experiments/predictions.jsonl)
- Sealed legacy history: 40 total rows (26 contracts, 14 resolutions, 12 pending contracts).
- Status: Always UNANCHORED. Managed via `LegacyLedger` or `legacy-report`.
- The v1 protocol does not hash resolution content, sequence numbers, row order, timestamps, or row completeness.
- Historical headline numbers (Brier 0.19, skill +0.21 on 14 resolved) were below the n >= 30 validity floor; calibration numbers on the public site are now suppressed.
- Byte-frozen historical artifact: do not edit, delete, or append to it.

### v2 ledger (experiments/predictions-v2.jsonl)
- Protocol `prepende/2`, stream `prepende-public-v2`.
- Status: UNANCHORED. Internal hash chain is valid (`internally_valid: true`, chain head `sha256:5ff625e6...`).
- Independent resolution: `independently_resolved: false`.
- 10 total rows: genesis (seq 0), legacy_import (seq 1), 4 contracts (seq 2-5), 4 forfeits (seq 6-9).
- Contract breakdown: 4 locked, 0 resolved, 4 forfeited, 0 pending, 0 void. Forfeit rate is 100% (4 of 4).
- All 4 contracts expired past their deadlines without observation pinned during the open resolution window, incurring locked non-resolution Brier penalty 1.0 (calibration suppressed below floor).
- All 4 contracts are permanently unanchorable because anchoring windows closed at forfeit time (`docs/V2_FORFEIT_POSTMORTEM_2026-08-23.md`).
- Calibration curve and skill headline are strictly suppressed (`curve_publishable: false`, `curvePublished: false`).
- Protocol v2 is delivered and live on main, not a WIP branch.

### Site and simulations
- Weekly rebuild runs via GitHub Actions (`.github/workflows/rebuild.yml`, last good rebuild 2026-08-31).
- All four research simulations (`sim_oim_benchmark.py`, `sim_oim_maxcut.py`, `sim_telepathy_hard.py`, `sim_telepathy_mock.py`) pass ("ok"). Sims require numpy; protocol core does not.
- Public site headline keeps calibration suppressed and both ledgers clearly marked UNANCHORED.

## 4. Remaining gaps (all verified in code)

1. **Unanchored v2 stream:** The public v2 stream status remains UNANCHORED (`anchored: false`). The four existing contracts missed their open anchoring windows and are permanently unanchored.
2. **No live TSA or external witness:** While the protocol supports Ed25519 external anchor statements and checkpoint requests, no live RFC 3161 TSA, OpenTimestamps, or third-party witness authority is connected in production (`docs/ANCHOR_PROVIDER_SPIKE_V2.md`).
3. **Self-resolved and forfeited public v2:** All four forward v2 contracts forfeited under deadline expiration. None was resolved with an independent resolver signature (`independently_resolved: false`).
4. **Sample size n below validity floor:** The valid resolved sample size is n = 0 on v2 (4 forfeits) and n = 14 on v1, well below the required n >= 30 floor. Calibration curves and skill metrics remain suppressed across public surfaces.
5. **Production agent path not wired:** Real Stella business agent commitments (turnkey quotes, delivery dates, conversion forecasts) are not yet connected to the ledger. The six pilot gates in `docs/PRODUCTION_PILOT_V2.md` remain unpaid.
6. **Names still collide:** The name "Prepende" is shared between this prediction protocol and the memory/orchestration brain runtime in `brain/` and Stella/Dealroom, requiring continuous disambiguation.

## 5. Roadmap (unpaid work only)

1. **Live external anchoring:** Connect an automated external timestamping authority (RFC 3161 TSA or Ed25519 checkpoint witness) to attest chain heads at lock time before commitment windows close.
2. **Scheduled independent resolvers:** Deploy automated resolver jobs that monitor resolution criteria, pin evidence digests inside open windows, and submit signed resolutions under verifier-trusted keys before deadlines expire.
3. **New forward prediction contracts:** Register and lock fresh forward contract batches with scheduled resolvers and live lock-time anchoring to build a genuine, verifiable track record past the n >= 30 floor.
4. **Wire the ledger into the Stella production agent path:** Implement the six pilot gates in `docs/PRODUCTION_PILOT_V2.md` for Stella commercial operations (shadow commitments on quotes, delivery dates, and conversion estimates; lock-time external anchors; independent outcome resolution).
5. **Disambiguate product naming:** Separate the public naming and package identities of Prepende Protocol from Prepende Brain to eliminate confusion across products.

## 6. House rules

- **The ledger is append-only.** Never edit, rewrite, or delete any JSONL row, even to fix a mistake. Append a correction, forfeit, or void under locked policies and note it.
- **Honesty floor is structural.** Misses and forfeits get headlined. Skips are not passes. UNANCHORED is not OK. No calibration curve or skill metric published below the n >= 30 floor in a segregated cohort. Retrospective and self-resolved rows are labeled in every report.
- **Never overclaim tamper-evidence.** State exactly what hash chaining guarantees and what requires verifier-trusted external anchoring. Internal chaining proves order and content integrity; only external anchoring proves timely existence; no local ledger proves absence of hidden streams.
- **Lock before you look.** Any new experiment locks its contracts and commits before the simulation or observation runs. Lock-to-resolve windows must be respected.
- **No secrets.** No hardcoded keys, no committed credentials, no private vault data.
- **No em dashes.** Do not use em dashes in copy or documentation.
