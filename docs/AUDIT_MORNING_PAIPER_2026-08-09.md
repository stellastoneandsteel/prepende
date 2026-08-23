# Morning Paiper — product audit, 2026-08-09

Audited under /prepende house rules: every claim below is either verified
against code, a command output, or a live endpoint this session, or it is
labelled UNKNOWN. Nothing is asserted from memory. Where a number is
selective or below its own floor, that is stated rather than smoothed.

Auditor: Claude Opus 5, in session with Ryan.
Repo state at audit: `morning-paiper` main `671a543`, 157 commits, clean.

---

## 1. Verdict

The product is **shippable but not shipped**. Two merges (#103, #104) are
green in CI and sitting undeployed behind an approval gate that is working as
designed. The bias meter — the feature this session rebuilt — is correct and
honest in code, and its real-world coverage is **UNKNOWN and currently
unmeasurable**, which is itself the largest open risk because a locked
prediction depends on measuring it within 7 days.

The most serious finding is not in the product. It is that **the published
calibration record overstates itself** (§5), and that the ledger's own
tamper-evidence is weaker than the project claims (§5.3).

---

## 2. Deployment state — production is two merges behind

| | |
|---|---|
| Netlify production deploy | `6a78e47f1a77aac449a19287`, state `ready` |
| Deployed commit | `0634898ac4edbaa179b75e6c78439f349b22860f` |
| Repo `main` | `671a543` |
| Drift | PR #103 (bias popover) and PR #104 (home copy) |

This is **not a fault**. `AGENTS.md` requires production builds to carry
provider-owned `DEPLOY_APPROVED_PR_ID` and `DEPLOY_APPROVED_MERGE_SHA` bound
to approved merged-PR metadata, plus a build-scoped read-only
`MORNING_PAIPER_GITHUB_READ_TOKEN`. Manual and local deploys are unsupported.
The guard held; the merges simply have not been approved.

Verified live against production this session:

- `GET /bias/` → **200** (old page still served; the 301 ships with #103)
- `GET /guide/` → no `#language-pressure` anchor yet
- home → still 1 × `href="/bias/"`
- `POST /v1/bias/link` → **401** (route absent on the deployed build; falls
  through to the auth handler)
- `POST /v1/bias/check` → **200** with a real reading, confirming the
  public-endpoint pattern the new route follows

**Action required by the owner, not by an agent:** set
`DEPLOY_APPROVED_PR_ID=103` and `DEPLOY_APPROVED_MERGE_SHA=f41b833`
(then #104 / `671a543`).

---

## 3. The bias meter as shipped in #103

### 3.1 What it actually does

`rateBias` counts loaded language, absolutes, unattributed authority and heat
punctuation against named attribution, hedging and counterpoint, per 100
words, through a compressive exponential. It is pure — no DB, no network, no
env — and deterministic. Locked by `biasMeter.test.mjs` including band
calibration fixtures.

### 3.2 Where it refuses, correctly

- **Under 40 words → no score.** A headline cannot carry a reading.
- **No political-lean claim.** Stated in the method string shipped with every
  reading, and asserted by test on the marketing surface.
- **No `paywalled` verdict anywhere.** From outside, a paywall, a bot block
  and a consent interstitial are one HTTP refusal. Failure reasons are
  transport-only: `invalid_url`, `blocked`, `unreachable`, `not_html`,
  `no_prose`, `too_short`.

That last one replaced a classifier that scanned the article's own text for
`subscribe`, `register`, `premium`, `login` and discarded good readings as
paywalled. A story about voter *registration* would have returned nothing.
This was caught before it ever deployed.

### 3.3 Privacy posture

Fetched article bodies are rated and dropped. Only the reading is cached
(1h success, 15m failure), keyed by URL hash. Our own articles rate from the
stored draft rather than a self-fetch — a self-fetch of `/a/:slug` would read
the article page's own bias section, whose vocabulary is the meter's own
lexicon, straight back into the score.

### 3.4 Verified interaction

Exercised in a real browser against the built assets: Escape / backdrop /
close-button all dismiss and return focus to the chip; scroll lock releases;
the refusal path renders reason-only with no score; a late response cannot
paint over a closed dialog; at 1280px the panel centres to the pixel.

### 3.5 UNKNOWN

**What share of outside articles can actually be read.** Publisher
bot-blocking, paywalls, consent walls and JS-only rendering are the failure
modes and their combined rate has never been measured. This is pre-registered
as contract `868479240ce9c83b`.

---

## 4. Engineering hygiene

| Check | Result |
|---|---|
| Deterministic tests | 1201 / 1201 |
| Provers | 9 / 9 (`typecheck`, `check:app`, `verify:pwa`, `verify:voice`, `security:artifact`, `verify:source`, `verify:boundary`, `verify:security-healing`, `verify:onboarding-contract`) |
| CI on both merges | green |
| Working tree | clean |

### 4.1 Git history was flattened and restored during this session

`origin/main` was reset to a parentless root commit (`f7fd0bd`, 461 files /
152,689 insertions), collapsing published history from 146 commits to 3. No
content was lost — only provenance. Restored via `5ac07f6`, a commit carrying
the then-current tree **exactly** with the pre-flatten tip `d9b7b47` as a
second parent, making the push a fast-forward rather than a force-push, so no
post-flatten PR was discarded. Tag `history-before-flatten` marks `d9b7b47`.

**The cause was never identified.** The local reflog shows only
`reset: moving to origin/main`. The repo runs a daily-improvement automation
and multiple concurrent agent sessions. **This can recur.**

### 4.2 Worktree sprawl

**48 worktrees** registered. Three carry uncommitted work:

- `morning-paiper-bias-fixes` — the codex session's own `/v1/bias/link` +
  `extractArticleBody` implementation, **now superseded by #103**, plus an
  untracked `.prepende/bias-popover-redirect-lock-2026-08-09.md`
- `.claude/worktrees/elated-cori-f9b834` — modifies
  `scripts/deploy-target-contract.mjs` and
  `scripts/smoke-morning-paiper-deploy-target.mjs`. **Deploy tooling. Not
  audited here. Do not discard without reading.**
- `/private/tmp/mp-netlify-repro.Ej3zS3` — untracked `node_modules` only

Left in place. Discarding another session's uncommitted work is the owner's
call, not an agent's.

### 4.3 Convergent lock, independently satisfied

The codex worktree contains a hash-locked claim
(`canonicalClaimSha256: d072717b…`) written at 17:51Z specifying: public
`POST /v1/bias/link`, card-level button + `role="dialog"` popover reusing the
same reading output, `/bias/` deprecated and redirected to
`/guide/#language-pressure`, and preserved security posture (no body storage,
SSRF hardening, rate limiting).

**What shipped in #103 satisfies every clause**, arrived at independently.
Two agents converging on the same design is weak evidence the design is
right; it is not evidence either implementation was verified, and only one
was.

---

## 5. The ledger audits worse than the product

### 5.1 Four open contracts, all forfeit-at-Brier-1

| contract | p | due | window |
|---|---|---|---|
| `ec64b26a95f5168c` | 0.90 | 2026-08-11T16:00Z | **2 days** |
| `01be3607ec83f768` | 0.40 | 2026-08-11T16:00Z | **2 days** |
| `adc69114c1eeccf8` | 0.65 | 2026-08-11T16:00Z | **2 days** |
| `868479240ce9c83b` | 0.65 | 2026-08-16T23:59Z | 7 days |

Every one carries `nonresolution_policy: forfeit @ penalty 1`. A missed
deadline scores as maximally wrong. Three resolve on Monday-night X trend
data; that is a manual observation with a hard window.

### 5.2 The coverage contract has a dependency it cannot control

`868479240ce9c83b` resolves via `scripts/measure_bias_link_coverage.mjs`,
which measures through the deployed `/v1/bias/link`. **That endpoint is not
deployed** (§2), and deployment requires owner approval. If approval does not
land with enough margin to harvest N≥30 and measure before 2026-08-16, the
contract forfeits at Brier 1 **for a reason unrelated to the prediction's
accuracy**.

This is a design flaw in my own lock: I bound a due date to work gated on
another party's approval. Recorded, not corrected — the ledger is append-only
and the regime is hashed. The lesson belongs in the next lock, not this one.

### 5.3 The published record overstates itself

From the v1 legacy report, this session:

```
locked predictions   : 26
resolved             : 14
pending              : 12
unresolved rate      : 46.2%
verification status  : UNANCHORED
trusted anchor       : NO
independent resolver : NO
```

- **46.2% of the book never resolved.** A headline Brier computed over the 14
  that did is a selected sample, and selection is not random when the
  selector is the predictor.
- **n = 14 is below the project's own stated n ≥ 30 floor** for publishing an
  aggregate.
- **UNANCHORED, self-resolved.** Timestamps are caller-supplied and hashed
  in, so backdating self-verifies. Same party locks and resolves. Free-text
  resolution rules are never executed.
- Legacy v1 **does not hash resolutions or commit row order/completeness** —
  the tool says so itself in its warnings.

The v2 chain verifies clean with no warnings, which is a real improvement.
It does not retroactively fix the v1 corpus the public headline rests on.

---

## 6. Ranked risks

| # | Risk | Severity | Owner action |
|---|---|---|---|
| R1 | 3 contracts forfeit in 2 days without manual X-trend resolution | **High** | Resolve Mon night |
| R2 | Coverage contract forfeits by 2026-08-16 if deploy is not approved | **High** | Approve #103/#104 deploy |
| R3 | Published calibration headline is selective and below its own floor | **High** | Correct or caveat publicly |
| R4 | History flatten cause unidentified; can recur | Medium | Investigate automation |
| R5 | Ledger unanchored and self-resolved | Medium | Roadmap item 1 |
| R6 | 48 worktrees, 3 dirty, one touching deploy tooling | Low | Triage and prune |

---

## 7. What this audit does not cover

Stated so the gaps are not mistaken for clean bills of health:

- **Supabase RLS posture** — not inspected this session
- **Billing and entitlement paths** — not exercised
- **Email deliverability** — not tested
- **The 44 clean worktrees** — not reviewed for stale branches
- **Real reader behaviour** — there is no pageview instrumentation in the
  repo at all, which is why the original "does the popover get used more than
  the page" prediction was unlockable and had to be replaced
