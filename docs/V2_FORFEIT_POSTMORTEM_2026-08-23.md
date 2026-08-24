# Why every v2 contract forfeited, and why the stream cannot be anchored now

2026-08-23. Written against `experiments/predictions-v2.jsonl` at chain head
`sha256:5ff625e6`, 10 rows, `internally_valid: true`, `status: UNANCHORED`.

## The record

Four contracts. Four forfeits. Zero resolutions.

| contract | claim | deadline | forfeited | reason code |
|---|---|---|---|---|
| `ec64b26a95f5168c` | p=0.90 | 2026-08-11T16:00Z | 2026-08-12T00:01:06Z | `deadline_expired` |
| `01be3607ec83f768` | p=0.40 | 2026-08-11T16:00Z | 2026-08-12T00:01:10Z | `deadline_expired` |
| `adc69114c1eeccf8` | p=0.65 | 2026-08-11T16:00Z | 2026-08-12T00:01:14Z | `deadline_expired` |
| `868479240ce9c83b` | p=0.65 | 2026-08-16T23:59Z | 2026-08-23T18:36:43Z | `deadline_expired` |

Every one carries a Brier penalty of 1 under the locked nonresolution policy.
This is a forfeit rate of 4 out of 4 and it is not a calibration benchmark,
because a forfeit measures whether the machinery ran, not whether the forecast
was any good. The distinction matters in both directions: it does not excuse the
record, and it does not let the record be read as evidence about accuracy.

The rate stays published. It is not suppressed, averaged away, or moved behind
a caveat about being early. Zero of the four says something true about this
project, and what it says is not about forecasting skill.

## Why

All four share one reason code and two distinct causes.

**Nothing was scheduled to resolve them.** The first three shared a deadline of
2026-08-11T16:00Z and were forfeited by hand at 00:01 the following day. No job
existed to pin an observation inside the resolution window, so the window closed
on all three at once. The resolution rules were written to read hourly
`trends24.in` snapshots during a fixed evening window — evidence that exists
only while it is being generated. Missing that window does not delay the
resolution, it destroys the evidence.

**The fourth could not resolve even if something had run.** Its rule measured
through `/v1/bias/link`, an endpoint that was not deployed and whose deployment
needed an approval that did not land. The 2026-08-09 audit recorded this as a
design flaw in the lock: a due date bound to work gated on another party's
approval. That audit also flagged that the resolver script's own digest was
never pinned in the contract, so even a timely run would have resolved under an
unpinned evaluator.

Recorded, not corrected. The ledger is append-only and none of this changes a
row.

## The stream cannot be anchored now, and that is the protocol working

`prepende verify` reports `UNANCHORED`. Tested today whether that can be closed
retroactively, on a copy of the ledger, not the ledger itself:

1. `checkpoint` over the current head — accepted.
2. `anchor-request` for that checkpoint — accepted.
3. A well-formed Ed25519 anchor statement, signed, added under a trusted key —
   accepted with **zero errors**.

Result: `status: UNANCHORED`, `anchored: false`, all four contracts still listed
in `unanchored_contracts`.

The anchor is valid and covers nothing. `Ledger.verify` counts an anchor toward
a contract only when `issued_at <= anchored_at < latest_lock_time`, and
`latest_lock_time` is the contract's resolution or forfeit. Three of those
windows closed on 2026-08-12 and the fourth on 2026-08-23. Every window is shut.

That is the point. An anchor is a third party attesting that the chain head
existed at a time; produced after the outcome it attests to nothing, and the
verifier declines to pretend otherwise. **These four contracts are permanently
unanchorable, and appending anchor rows now would add bytes to a public chain
without adding evidence.** So none were appended.

Anchoring is not a step that can be caught up on later. It has to happen while
the window is open, which means at lock time.

## Before locking anything else

No new contracts are locked by this note. Three things do not exist yet, and
each one is a cause of a forfeit above:

1. **A scheduled resolver.** Something that runs inside the resolution window
   and pins the observation, with a receipt. Every forfeit here is this gap.
2. **A pinned resolver digest.** A rule that names a script must pin that
   script's SHA-256 at lock time, so the resolver that runs is provably the
   resolver that was agreed to.
3. **An anchor at lock time**, from a party that is not the predictor, inside
   the window where it means something.

A fourth is a judgement rather than a mechanism: do not bind a deadline to work
that needs someone else's approval. Contract four did, and forfeited for a
reason that had nothing to do with whether the claim was right.

Until those exist, locking another contract adds another forfeit, and a fifth
forfeit is not evidence about calibration either.
