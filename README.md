# Prepende

**Verifiable proof that a predictor's confidence is trustworthy.**

Prepende makes an AI (or a human) commit a **falsifiable prediction before the outcome
is known**, with its scoring rule *and* evaluation regime locked into a content hash.
After that you cannot move the goalposts: any edit breaks the hash, and resolving under
a changed regime is refused. Over many predictions the ledger produces a real
**calibration curve** — when it says 70%, does it happen ~70% of the time?

Pure standard library. No dependencies. MIT licensed.

## Why

AI systems state confidence constantly — agent self-scores, "I'm 90% sure," eval numbers —
and almost none of it is *verifiably* calibrated, because nothing proves the prediction was
made before the outcome. Prepende turns "trust the AI's confidence" from faith into
something you can check by hashing.

The hard part of calibration isn't the math — it's **not cheating after the fact.** That's
enforced here by construction:

- **Tamper-evident:** the prediction + resolution rule + eval regime are SHA-256 hashed at
  lock time. Change any of them and `Ledger.integrity()` flags it.
- **No goalpost-moving:** resolving under a different `eval_regime` than the locked one is
  refused (`RetrofitError`).
- **External timestamp:** the ledger is JSONL — commit it to git (or a timestamp service) and
  the history proves each lock preceded its outcome.

## Quickstart

```python
from prepende import Ledger, lock_prediction, build_report

L = Ledger("ledger.jsonl")
c = lock_prediction(
    predictor="gpt-x",                       # any model id or "human:alice"
    question="Will the next release pass CI on the first attempt?",
    kind="probability", claim={"p": 0.8},
    resolution_rule="y=1 if first CI run is green",
    eval_regime="ci-pipeline-v3",
)
L.lock(c)                                     # commit BEFORE the outcome
# ... later ...
L.resolve(c.cid, {"y": 1}, "ci-pipeline-v3") # regime MUST match the lock
print(build_report(L))
```

CLI:

```bash
python3 -m prepende --ledger ledger.jsonl lock \
  --predictor gpt-x --question "..." --claim '{"p":0.8}' \
  --rule "y=1 if green" --regime ci-v3
python3 -m prepende --ledger ledger.jsonl resolve --cid <id> --outcome '{"y":1}' --regime ci-v3
python3 -m prepende --ledger ledger.jsonl report
python3 -m prepende --ledger ledger.jsonl plot --out calibration.svg
```

Demo + tests:

```bash
python3 -m prepende.demo     # report + reliability SVG + anti-retrofit proofs
python3 -m prepende.tests    # test suite
```

## Metrics

Brier score, log loss, skill vs base-rate baseline, **ECE / MCE**, the Murphy **Brier
decomposition** (reliability − resolution + uncertainty), per-bin **Wilson 95% CIs**, and a
reliability-diagram **SVG**.

## Prediction kinds

- `probability` — `{"p": 0.7}` → outcome `{"y": 1|0}` (Brier, log loss, calibration).
- `numeric` — `{"value": 0.9, "lo": 0.85, "hi": 0.95}` → `{"value": 0.78}` (MAE, CI coverage).
- `categorical` — `{"label": "a", "p": 0.6}` → `{"label": "b"}` (scored via correctness).

## Honest limits

This makes predictions **falsifiable and scorable, not correct.** A calibration curve only
means something at **n ≥ ~30** across independent domains, and self-scoring is weak evidence
to outsiders — the cheap fix is to **commit the ledger publicly** so the locks are externally
timestamped. The asset is the accumulated, checkable track record; the code is just the rails.

## License

MIT © 2026 Ryan Amerio

## This repository
- `prepende/` — the calibration tool (pure standard library, no deps).
- `docs/index.html` — the project site (GitHub Pages: Settings -> Pages -> /docs).
- `experiments/` — runnable simulations + the data behind the site's charts (need numpy).

Honest scope: the experiments are software results, not hardware claims; the larger
ideas are labeled hypotheses; nothing here claims a "singularity." See the site.
