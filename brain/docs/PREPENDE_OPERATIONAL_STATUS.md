# Prepende operational status

`operational-status` is the one read-only view across the private brain,
standalone public Protocol v2, private trust authority, pilot projection, and
recovery receipts.

```text
./bin/prepende operational-status [--json] [--scope SCOPE]
  [--protocol-repo PATH] [--trust-repo PATH] [--online]
```

Repository locations are never inferred from sibling directories. The command
uses an explicit flag first, then `PREPENDE_PROTOCOL_REPO` or
`PREPENDE_TRUST_REPO`, and otherwise reports that source as `notConfigured`.
The Protocol path must identify the `prepende` repository and the trust path
must identify the `prepende-trust-services` repository plus its safe pilot
status adapter. A wrong identity is an invalid invocation, not degraded
evidence. Product-specific pilot paths remain inside the private trust adapter.

Default mode is offline and byte-for-byte read-only. It makes no model call,
does not probe connectors, rebuild an index, dispatch a workflow, or write
memory. The `--online` option adds bounded GitHub reads for the release assets,
remote heads, workflow catalog, and remote pilot projection. A failed online
read remains `unknown`.

JSON output uses `prepende-operational-status-v1` and keeps `brain`, `protocol`,
`trust`, `pilot`, `recovery`, `online`, and `sources` separate. Section states
are `ready`, `degraded`, `blocked`, `unknown`, `notConfigured`, or
`notApplicable`. The embedded v0.2 protocol is always non-authoritative and can
never satisfy Protocol v2 validation.

The pilot ledger is verified in an isolated process using the explicitly
configured Protocol v2 repository. `state.json` is only a projection. A count
or final-verification mismatch is blocking. Pilot counts are commissioning
evidence; fewer than 30 resolutions cannot satisfy the calibration floor or
justify increased autonomy.

Exit status is `0` only when every applicable top-level section is ready, `1`
for a valid degraded/blocked/unknown result, and `2` for invalid arguments,
unsafe repository identity, or a collector/schema failure.
