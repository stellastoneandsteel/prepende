# The ledger that refuses to let me move the goalposts

Article for X (Morning Paper community). The section below the rule is the
paste-ready post. Banner: `2026-08-09-prepende-banner.png`.

---

I built a prediction ledger that refuses to let me cheat, and it just caught me being wrong at 72% confidence.

Here is the mechanism. Before an experiment runs, the prediction gets locked: the claim, the exact resolution rule, and the evaluation regime are hashed together into a single SHA-256 commitment. Not just "I predict X." The whole deal: what counts as true, how it will be measured, under which harness, with which baselines.

Later, when it is time to resolve, the resolution must reference that hash and match the locked evaluation regime exactly. Change the regime, tweak the baseline, swap the metric that made you look bad? The ledger raises an error and refuses to score you. Moving the goalposts is not discouraged. It is structurally impossible to do quietly.

I looked for prior art before claiming novelty. Prediction markets commit to claims. Eval harnesses pin metrics. Nobody I could find hashes claim plus resolution rule plus evaluation regime into one commitment and then refuses resolution under a changed regime. That combination is the whole product.

The receipts, from the public ledger:

26 predictions locked. 14 resolved, misses included. Batch 8 headline: I predicted at p=0.72 that measurement averaging would rescue a noise-fragile reservoir computing advantage. It did not clear the locked bar. Logged as a MISS, permanently, because the hash does not care about my feelings.

Across the 13 scored binary calls the Brier score is 0.187 against a 0.237 base-rate baseline. Honest caveat: that is below the n>=30 floor the README itself sets for validity claims, and every row so far is self-resolved by the same party that locked it. Three early rows are retrospective dev self-tests and are labeled as such, never pooled into forward numbers.

That self-resolution gap is exactly what Protocol v2 (just merged) attacks: every row sequence-numbered and hash-chained, deterministic evaluators locked at commitment time instead of prose rules, deadlines with non-resolution penalties so silence costs you, and external checkpoint signatures to attest which prefix of the ledger existed by when. It still does not claim hidden unregistered streams cannot exist, or that any named authority is honest. It says precisely what it covers and nothing more.

Why bother? Because AI agents are about to make millions of commitments: quotes, delivery dates, conversion estimates, risk calls. "Trust my confidence" is not a security model. A hash-locked track record you cannot backfill is.

Pure Python stdlib, zero dependencies, MIT.

https://github.com/stellastoneandsteel/prepende
