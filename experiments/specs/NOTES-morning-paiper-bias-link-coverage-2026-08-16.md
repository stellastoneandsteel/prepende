# Post-hoc notes: morning-paiper-bias-link-coverage-2026-08-16

**This file is NOT part of the pinned regime.** The contract
`sha256:868479240ce9c83b…` pins only
`experiments/specs/morning-paiper-bias-link-coverage-2026-08-16.md` at
`sha256:403a50016256377b6b182e7b0002fd65ac9b053d009c8d73066e4aed242e93cd`.
Nothing here changes that document, and nothing here may be read as amending the
regime. It exists because a defect in the regime was found after lock and
burying it would be worse than recording it.

## The defect: the resolver script was never actually pinned

The spec says, at line 62:

> If the script is edited after this contract is locked, the edit is a regime
> change and resolution is refused.

There is no mechanism behind that sentence. The contract's
`evaluation.artifacts` carries exactly one entry, `role: regime-spec`, pointing
at the spec document. **No digest of
`scripts/measure_bias_link_coverage.mjs` was recorded at lock time**, so nothing
can detect an edit, and the refusal the spec promises cannot fire.

The timing makes this concrete rather than theoretical:

| Event | Timestamp (UTC) |
|---|---|
| Contract `868479240…` issued | 2026-08-09T20:17:39Z |
| Only commit touching the script (`330cd64`) | 2026-08-09T22:01:11Z |

The script's single commit lands **1h44m after the lock**. That is consistent
with the script having been written first, locked against in the working tree,
and committed later unchanged — which is probably what happened. It is equally
consistent with an edit in that window. **Because no digest was taken at lock
time, the two cannot be distinguished, and I will not assert the benign reading
as fact.**

## What was observed, and when

Recorded 2026-08-12T00:03:11Z, two days after lock, by reading the working tree:

```
sha256  1c0366c99ec959cc63db21f33ffda19f4c02f0b0c20e8238942aeb68b05798ff
file    morning-paiper/scripts/measure_bias_link_coverage.mjs
commit  330cd643124b06ff86d5f20d1f3dc76ae97bc33e (clean, no uncommitted diff)
```

This digest establishes only that the script has not changed **since
2026-08-12**. It says nothing about the lock-to-commit window. Treat it as a
tripwire from this date forward, not as retroactive tamper-evidence.

## Consequences for resolution

- The contract cannot be voided: `void_policy.allowed_reason_codes` is empty,
  by design. Void is not an escape hatch here.
- The deadline is real: 2026-08-16T23:59:00Z, `forfeit` at Brier penalty 1.
  Three sibling contracts already forfeited at full penalty on 2026-08-11 for
  exactly this reason.
- Therefore the honest path is to **resolve on time and disclose this gap in the
  resolution evidence**, rather than to treat an unpinned resolver as if it had
  been pinned.

When resolving, the evidence document should carry, alongside the fields the
spec requires, an explicit `regime_caveat` naming this file and stating that the
resolver script's integrity is verified only from 2026-08-12 forward.

## The fix, for every future contract

A regime clause that names a file must pin that file's digest in
`evaluation.artifacts` at lock time, with a role such as `resolver-script`.
A sentence in prose is not a gate. This one was not, and the corpus should not
repeat it.
