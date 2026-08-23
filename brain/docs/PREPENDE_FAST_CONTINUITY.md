# Prepende Fast Continuity V2

Status: local implementation candidate

Fast continuity is a bounded, no-model handoff. It proves what an operator may
safely resume; it does not pretend that a responsive CLI proves deployment or
disaster recovery.

## Commands

```bash
npm run prepende -- context-fast "<goal>" --json --scope <scope>
npm run prepende -- context-fast "<goal>" --json --scope <scope> --profile recovery
npm run prepende -- operator start "<goal>" --scope <scope> --profile coding
python3 scripts/verify_prepende_recovery.py --json --scope <scope>
python3 scripts/verify_prepende_recovery.py --print-template
python3 scripts/prepende_recovery_receipts.py template --gate netlify_recovery
python3 scripts/prepende_recovery_receipts.py build --scope <scope> --dry-run
```

Supported continuity profiles are `general`, `coding`, `deployment`, and
`recovery`. The default is `general`. A profile changes which observed facts
block planning; it never grants tools, deployment, provider, memory-promotion,
or external-action authority.

## Verdicts

The legacy top-level `ok` remains transport compatibility. New consumers should
read all four V2 verdicts:

| Verdict | Meaning |
| --- | --- |
| `transportOk` | The scoped status subprocess returned a valid result. |
| `continuityReady` | Critical local state exists to resume safely. |
| `planReady` | The selected profile's execution prerequisites are present. |
| `recoveryProven` | A fresh manifest proves all ten independent recovery gates. |

`ok:true` must not be interpreted as any of the other three verdicts.

## Fast-lane boundary

`context-fast` may read local repository state, operator receipts, Prepende
status, and the cached recovery manifest. It must not:

- call a model;
- contact GitHub, Netlify, Supabase, or another provider;
- create a backup or restore a system;
- write durable memory;
- execute an external action.

Slow collectors and restore drills produce evidence receipts separately. The
fast packet reports their availability, freshness, and digests.

The packet expires after five minutes by default and contains:

- schema, packet, goal, and source digests;
- scope and requirement profile;
- repository branch, HEAD, dirty count, upstream, and remote presence;
- latest scope-bound operator checkpoint;
- blockers classified by severity and the verdicts they block;
- cached recovery-manifest evaluation;
- observation times for every source.

Graphify is an optional projection, so staleness is advisory while the vault and
lexical RAG path remain healthy. Connector availability is task-dependent. An
unavailable status lane or lexical knowledge path blocks continuity.

The `coding` profile has one narrow exception: an intentionally empty tenant
index may proceed when all RAG source, index, chunk, embedding, and missing
embedding counts are zero and `stale:false`. The packet emits
`rag_empty_coding_scope` as an advisory and keeps `lexical_ready:false`.
General, deployment, and recovery profiles still block. Missing, malformed,
stale, partial, or internally inconsistent RAG status also blocks. Operator
start and sandbox commands require transport, scope, profile,
`continuityReady`, and `planReady` to match before writing an active receipt.

## Ten-gate recovery manifest

The cached manifest defaults to:

```text
.engram/continuity/recovery-manifest.json
```

`PREPENDE_RECOVERY_MANIFEST` may point to a different reviewed cache. The
manifest is proven only when it is unexpired and every required gate is `pass`
with at least one structured evidence receipt. Each receipt must identify its
receipt ID, observation time, and SHA-256 digest, and must be no more than 31
days old:

1. `inventory`
2. `source_recovery`
3. `work_in_progress_recovery`
4. `prepende_recovery`
5. `assistant_continuity`
6. `netlify_recovery`
7. `supabase_recovery`
8. `credential_recovery`
9. `lost_machine_drill`
10. `failure_detection`

Unknown gates do not receive partial credit. Netlify deploy history is not a
source or database backup. Supabase project health is not restore proof. Local
archives on the same physical disk are not offsite recovery.

The manifest itself is bound to one exact tenant/workspace scope. Assembly
ignores receipts from all other scopes before validation, and continuity
refuses a manifest whose scope does not match the requested handoff.

The verifier is read-only. It validates a cached evidence manifest; it does not
perform destructive restoration or turn fixture evidence into production proof.

## Receipt production

The manifest is assembled from immutable gate receipts under
`.engram/continuity/recovery-receipts/`. Do not hand-edit the manifest. Use the
receipt producer CLI:

```bash
# Get the exact observation contract for one controlled drill.
python3 scripts/prepende_recovery_receipts.py template --gate supabase_recovery

# Record a completed collector/drill observation and its local evidence files.
python3 scripts/prepende_recovery_receipts.py record --input <observation.json>

# Import the latest existing isolated Prepende restore drill.
python3 scripts/prepende_recovery_receipts.py collect-restore-drill --scope <scope>

# Select the newest valid receipt per gate and write the cached manifest.
python3 scripts/prepende_recovery_receipts.py build --scope <scope>

# Independently verify the receipt references, digests, freshness, and gates.
python3 scripts/verify_prepende_recovery.py --json --scope <scope>
```

Every gate has a fixed proof class, allowed producer kind, and required check
IDs in `operations/recovery_receipts.py`. A passing receipt requires all checks
to pass, a preserved evidence artifact, an isolated or read-only target,
`secretsStored:false`, `productionMutated:false`, and no durable memory write.
The newest valid receipt wins, including a failure newer than an older pass.
Tampered, missing, expired, path-escaping, or digest-mismatched receipt
references fail closed.

See `docs/PREPENDE_RECOVERY_RECEIPTS.md` for the exact ten-drill operating
contract, especially the Netlify, Supabase, credentials, and lost-machine
boundaries.

A hosted or replacement-host automation may collect bounded technical
candidate evidence, but a successful job is not a passing recovery drill. A
candidate cannot by itself prove independent-host use and destruction,
complete hosted-service reconnection, isolated database restoration,
disposable-credential lifecycle, owner credential recovery, or off-device
artifact readback, and it must not record a passing recovery observation.

Provider-specific workflow, trust-policy, temporary-secret, runtime, and local
evaluator instructions belong only in the installation's private operator
runbook. A passing gate requires independently authenticated evidence that
binds provider execution, artifact integrity, final readback, and every required
acceptance check. Missing or unbound evidence stays red and never upgrades
`recoveryProven`.

## Acceptance

Run:

```bash
python3 tests/smoke_context_fast.py
python3 tests/smoke_continuity_v2.py
python3 tests/smoke_recovery_verifier.py
python3 tests/smoke_recovery_receipt_pipeline.py
```

The implementation is complete when the fast packet remains bounded, transport
compatibility remains intact, stale or missing evidence produces explicit
verdicts, and the verifier refuses missing, expired, failed, or unknown gates.

Operational recovery remains unproven until independent off-device and hosted
restore receipts populate the real manifest.
