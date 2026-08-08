# Prepende Protocol v2 threat model

## Protected operations

Given the complete stream and applicable external receipts, verification detects edits to
any contract, outcome, evidence, note, timestamp, signature, or disposition. It also detects
row insertion, reordering, duplicate sequence numbers, conflicting logical events, interior
deletion, and truncation that removes the only qualifying anchor from the presented stream.

Caller-supplied issuance timestamps are not part of the public API. A lock is useful as
outside evidence only after a verifier-trusted authority signs a checkpoint that covers it.
An anchor issued after the contract's resolution, void, or locked deadline does not
retroactively qualify that contract as anchored.

A terminal resolution or void is not complete merely because its row exists. A trusted
checkpoint must cover it, the checkpoint cannot predate any covered event timestamp, and
the authority timestamp must fall at or after the terminal timestamp and no later than the
locked deadline. This makes local clock rollback and late witnessing visible.
For signed resolution, the separate resolver signs the ledger-prepared statement.

Deadlines and non-resolution penalties are locked contract fields. An overdue open contract
is `INCOMPLETE`; it cannot disappear from report denominators. Void is limited to predeclared
reason codes and an authorized signed resolver.

## Required external assumptions

- A verifier obtains the stream and trust stores through an authentic channel.
- Anchor and resolver private keys remain controlled by their named authorities.
- Trusted authorities report time and outcomes according to their stated policies.
- Evidence documents stored inline are safe to publish. Sensitive production evidence needs
  a separate privacy-preserving transport before that use case is enabled.
- Stream registration defines which issuer identity and stream are being evaluated.

## Explicit non-goals

The protocol does not prove that no hidden unregistered ledger exists. It does not prove that
an authority is honest, that an input data source describes reality, or that correlated
forecasts form an independent calibration sample. It does not grant an agent permission to
act. Action approvals remain a separate control plane.

The v1 corpus remains self-attested. A v2 import commits its exact bytes and provenance but
does not retroactively protect v1 resolution rows or timestamps.
