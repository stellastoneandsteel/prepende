# Prepende Protocol

**Registered, chained prediction commitments with explicit external trust.**

Prepende Protocol v2 locks a prediction together with its deterministic evaluator,
evaluation artifacts, deadline, resolver policy, and non-resolution penalty. Every later
event is sequence-numbered and hash-chained. External checkpoint signatures can establish
which registered-stream prefix existed by an authority-attested time.

This is a release candidate. It distinguishes internal integrity, external anchoring,
registered-stream completeness, and independent resolution. It does not claim that hidden
unregistered streams cannot exist or that a named authority is honest.

## Distribution

[GitHub Releases](https://github.com/stellastoneandsteel/prepende/releases) is the only
supported distribution channel. Each release contains a wheel, source distribution, and
`SHA256SUMS`; verify the downloaded artifact before installation. Prepende is not published
to PyPI. See the [publishing contract](docs/PUBLISHING.md).

## Brain runtime

The separately versioned, product-neutral brain runtime lives in [`brain/`](brain/README.md).
It is imported as a reviewed, history-free snapshot from the private operating workspace;
owner vaults, tenant data, runtime state, deployment material, credentials, receipts, and
private Git history are excluded by an exact default-deny inventory. Run it from this
repository with `./brain/bin/prepende`.

The Protocol package and its `prepende` executable remain authoritative and unchanged at
the repository root. Brain runtime code does not replace or satisfy Protocol v2 validation.

## Install locally

```bash
python3 -m pip install -e .
# Ed25519 anchor and resolver verification:
python3 -m pip install -e '.[signatures]'
```

No runtime dependency is required for canonicalization, chaining, validation, scoring, or
self-resolved streams. Ed25519 verification is an optional dependency.

## Create and lock

```python
from prepende import Ledger

ledger = Ledger.create(
    "ledger-v2.jsonl",
    stream_id="agent-a-production",
    registered_predictor="agent-a",
)

contract = ledger.lock_prediction(
    predictor="agent-a",
    model_version="model-2026-08",
    domain="ci-delivery",
    event_id="release-184-first-run",
    question="Will release 184 pass its first CI run?",
    kind="probability",
    claim={"p": "0.8"},
    resolution_rule="y=1 if the first recorded CI conclusion is success",
    evaluator={
        "type": "binary_value",
        "version": "1",
        "parameters": {"evidence": "first-ci-result", "field": "passed"},
    },
    evaluation={
        "id": "ci-pipeline-v3",
        "spec_digest": "sha256:" + "1" * 64,
        "artifacts": [],
    },
    resolution_due_at="2026-08-10T00:00:00Z",
    resolver_policy={"mode": "self", "authorized_key_ids": []},
    nonresolution_policy={"action": "forfeit", "metric": "brier", "penalty": "1"},
)
```

Numbers in hashed records use decimal strings. `created_at` and `resolved_at` are not public
arguments; the ledger assigns them inside the append flow.

## Resolve from pinned evidence

```python
ledger.resolve(
    contract.contract_id,
    evidence=[{
        "name": "first-ci-result",
        "uri": "urn:ci:release-184:first",
        "content": {"passed": True},
    }],
)
```

The evidence digest and outcome are recomputed during every verification. Signed resolver
flows use `prepare_resolution()` followed by `resolve_signed()` before the locked deadline.
When evaluation artifacts are declared, each artifact role must be supplied as an evidence
name with the exact locked URI and content digest, and `evaluator.parameters.evidence`
selects the hash-pinned document that drives the outcome.

## Checkpoint and verify

```python
checkpoint = ledger.checkpoint()
request = ledger.anchor_request(checkpoint["checkpoint_id"])
# The independent provider adds its own anchored_at and key_id, signs the full statement,
# and returns both values. Prepende then appends that receipt:
# ledger.add_anchor(provider_statement, provider_signature)

report = ledger.verify(
    trusted_anchor_keys={"example-tsa": trusted_key_record},
    external_anchor_receipts=receipts_obtained_independently,
)
print(report.status)  # OK, UNANCHORED, INCOMPLETE, or TAMPERED
```

Trust is supplied by the verifier. A key mentioned only inside a ledger is not automatically
trusted. Passing receipts obtained outside the ledger lets verification detect a presented
stream that omits a later known checkpoint. See [the protocol specification](docs/PROTOCOL_V2.md) and
[threat model](docs/THREAT_MODEL_V2.md). The
[provider spike](docs/ANCHOR_PROVIDER_SPIKE_V2.md) records why the RC uses an external
Ed25519 authority interface and why that is not a claim that a live public TSA is configured.
Signed outcomes count as independently resolved only after a trusted checkpoint externally
witnesses the terminal row at or after its timestamp and by the locked deadline while the
resolver key is valid. Revoked keys fail closed.

## Reporting floor

Reports keep protocol, predictor, model version, domain, prediction kind, evaluator and
artifact manifest, evaluation regime, provenance, and resolver class separate. Pending,
overdue, forfeited, and void contracts remain visible. Calibration
curves and skill headlines are suppressed below 30 resolved probabilistic predictions in a
single segregated cohort. The public rebuild additionally requires exactly one sufficient
forward cohort, a fully verified stream, and independently trusted resolution signatures.

## Legacy corpus

`experiments/predictions.jsonl` is frozen as byte-for-byte v1 history: 26 contracts, 14
resolutions, and 12 pending at the v2 cutover. V1 contract hashes still verify, but resolution
content, row order, completeness, and timestamps were not protected by the protocol. Use
`LegacyLedger` to inspect it. A v2 import event can commit its exact bytes without upgrading
those historical guarantees.

## Commands

```bash
python3 -m prepende --help
python3 -m prepende.tests
python3 -m unittest discover -s tests -v
python3 -m build
```

The research simulations under `experiments/` require numpy. The protocol core does not.

## License

MIT, copyright 2026 Ryan Amerio.
