# Prepende Protocol v2

Status: release candidate. Protocol identifier: `prepende/2`.

## Claim boundary

Prepende v2 creates an append-only prediction stream whose rows commit to their order and
content. It separates four claims:

- `internally_valid`: every row, event, sequence, and previous-row link verifies.
- `anchored`: every contract is covered by a checkpoint signed by an authority trusted by
  the verifier, with the anchor time after issuance and strictly before its resolution,
  void, or locked deadline.
- `complete_through`: the largest sequence committed by a verifier-trusted checkpoint.
- `independently_resolved`: every signed terminal event used for this claim verifies under
  a resolver key independently trusted by the verifier and is covered by a trusted external
  checkpoint at or after the terminal timestamp and no later than the locked deadline while
  that key is valid. A mixture containing self-resolved outcomes is not independently
  resolved.

The protocol can prove completeness only for one registered stream through an anchored
checkpoint. A verifier should retain receipts outside the ledger and pass them back during
verification; a valid trusted receipt with a missing checkpoint proves truncation or a fork.
The protocol cannot prove that an operator never created an unregistered private stream.

## Encoding and hashes

Rows are one canonical JSON object per UTF-8 line. Files end with a newline. Hashed values
allow JSON nulls, booleans, integers, strings, arrays, and objects with string keys. Binary
floating-point and decimal objects are forbidden. Forecast and observed numbers use finite,
non-exponent decimal strings with insignificant trailing zeros removed. Strings and keys are
Unicode NFC normalized, object keys are sorted, and JSON separators contain no whitespace.
Verifiers reject noncanonical row bytes and duplicate object keys rather than accepting an
equivalent permissive JSON parse.

Protocol timestamps use the literal UTC form `YYYY-MM-DDTHH:MM:SS[.ffffff]Z`. Alternate
date/time separators and timezone offsets are not canonical protocol timestamps.

All digests are lowercase `sha256:<64 hex>` values. Domain-separated bytes are:

```text
prepende-v2 NUL <ascii-domain> NUL <canonical-json-bytes>
```

Each row contains `protocol`, `seq`, `prev_hash`, `event_type`, `event`, `event_hash`, and
`row_hash`. `event_hash` uses domain `event/<event_type>`. `row_hash` uses domain `row` over
the other six row fields. The full 256-bit contract ID is authoritative. A shortened display
value is never accepted as an identifier.

## Stream and event semantics

Sequence zero is a genesis event with a stable stream ID, registered predictor, anchor
policy, and ledger-clock timestamp. Supported later events are:

- `legacy_import`: commits the complete bytes, byte count, row count, and Git provenance of
  the v1 corpus frozen at cutover. Its classification is always `legacy-self-attested`.
- `contract`: locks the genesis stream identity, predictor/model/domain identity, a unique
  logical event ID, the claim,
  deterministic evaluator, evaluation-spec digest and artifacts, deadline, resolver policy,
  non-resolution penalty, optional void reasons, provenance, and issuance time.
- `resolution`: stores inline evidence with its content digest and the outcome recomputed by
  the locked evaluator. Every evaluation artifact role must appear as an evidence name with
  the exact locked URI and content digest. It is self-resolved or carries an authorized
  detached signature.
- `forfeit`: records the locked penalty after the deadline. It cannot be added early.
- `void`: requires a reason locked in the contract and an authorized detached signature.
- `checkpoint`: commits the preceding sequence, row count, and chain head. Its timestamp
  cannot precede any semantic timestamp in the rows it covers.
- `anchor`: carries a detached signature over the exact checkpoint statement.

Every persisted contract must be the exact normalized protocol representation; raw values
that only become valid through lossy coercion are rejected. Every contract stream and
predictor must match the stream and predictor registered by genesis. Text identifiers
are normalized to Unicode NFC before uniqueness checks and hashing. One
`(predictor, domain, event_id)` tuple can appear only once. A contract can have only one
terminal event. Prepared signed resolutions and voids must also be appended by the deadline;
afterward, the stream must record a forfeit.

Every evaluator names the evidence document it reads in `parameters.evidence`; it does not
implicitly consume the first document. If the contract has evaluation artifacts, the
selected evidence name must be one of the hash-pinned artifact roles.
Terminal outcomes, evidence, reason codes, and notes are validated against exact protocol
types. In particular, JSON booleans are not accepted as integer outcome values, and
categorical outcomes must originate from string evidence.

## Trust and status

Signing keys do not become trusted merely because a ledger names them. Verifiers supply
separate anchor and resolver trust stores keyed by key ID. Each trust record contains an
Ed25519 public key and optional validity, expiry, and revocation timestamps. The optional
`signatures` package extra provides verification.

Revocation is fail-closed and retrospective in the reference verifier: once a trust record
contains `revoked_at`, no receipt under that key is accepted. This avoids allowing a
compromised key to create a newly signed but backdated receipt. Historical preservation
across rotation therefore requires an additional independently retained witness, not a
self-asserted event time.

Status precedence is:

1. `TAMPERED` for malformed rows, broken hashes/links, invalid evaluator results, or invalid
   signatures under a supplied trusted key.
2. `INCOMPLETE` for overdue open contracts, missing resolver trust, terminal events lacking
   a trusted external witness by the deadline, or business events beyond the latest trusted
   checkpoint.
3. `UNANCHORED` when one or more contracts lack a qualifying trusted anchor.
4. `OK` when the stream is internally valid, every contract is anchored strictly before its
   terminal event or deadline, every resolution or void is externally witnessed at or after
   its timestamp and by its deadline, no deadline is overdue, and the latest business event
   is checkpointed.

Self-resolved streams can be `OK` for chain and anchor completeness while still reporting
`independently_resolved: false`.

## Reporting

Reports keep cohorts separate by protocol, registered stream, predictor, model version,
domain, prediction
kind, evaluation regime ID, specification digest, full artifact manifest, complete
evaluator definition and parameters, provenance, resolver class, and the complete resolver,
non-resolution, and void policies.
All locked contracts remain in the denominator. Pending,
overdue, forfeited, and void counts are always visible. Probability forfeits contribute the
penalty locked in the contract to penalized Brier loss.

Calibration curves and skill headlines are suppressed below 30 resolved probabilistic
predictions in a segregated cohort. A sample count does not itself prove independence.
