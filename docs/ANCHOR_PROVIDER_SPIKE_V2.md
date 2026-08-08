# Protocol v2 anchor provider spike

Decision date: 2026-08-08. Status: release-candidate design decision, not a live service.

## Decision

The reference route is an external Ed25519 checkpoint authority behind the
provider-neutral `AnchorProvider` interface. Prepende emits a timeless request containing
the stream identity, checkpoint row hash, covered sequence and head, and row count. A
separate authority adds its own `anchored_at` and `key_id`, signs the complete canonical
statement, and returns the statement and detached signature. Verification uses a public key
and validity metadata supplied independently by the verifier.

This route is independently verifiable under the configured authority key and keeps the
base package dependency-free. It is not trust-minimized: the verifier still trusts the
authority's clock and key-control policy. Merely running the helper in the ledger process
does not create independence. Production requires a separate service/principal and a
receipt copy retained outside the ledger.

## Alternatives considered

- [RFC 3161](https://www.rfc-editor.org/rfc/rfc3161.html) is the standards-track X.509
  Time-Stamp Protocol. It is a strong next provider because the receipt can bind the
  checkpoint digest to a TSA time and certificate chain. It was not made the RC default
  because correct CMS, certificate-policy, and chain validation would add a substantial
  dependency and provider-policy surface.
- [OpenTimestamps](https://opentimestamps.org/) supplies client tooling and proof formats
  that can ultimately anchor a file digest into Bitcoin. It is attractive as a
  trust-minimized secondary receipt, but its pending-to-complete proof lifecycle does not
  match the RC's immediate issuance gate without additional state and retry semantics.
- [Sigstore Rekor](https://docs.sigstore.dev/logging/cli/) provides verifiable inclusion
  proofs and signed log heads for uploaded signed artifacts. It remains a candidate for
  public checkpoint transparency, but its current tooling is centered on software signing,
  and the [Rekor project](https://github.com/sigstore/rekor) is transitioning from v1 to a
  new log design. Protocol v2 therefore does not hard-code that API.

## Fail-closed boundary

No live authority, key, RFC 3161 TSA, OpenTimestamps calendar, or Rekor instance is
configured in this repository. The public v2 import consequently verifies as `UNANCHORED`.
A production caller must not show or act on a commitment until it has obtained and retained
a qualifying external receipt. Publication and production-provider commissioning remain
separate approval gates.
