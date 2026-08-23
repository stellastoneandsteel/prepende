# Prepende Recovery Receipt Runbook

Status: receipt infrastructure implemented; operational drills remain evidence
dependent.

The recovery verifier answers whether current proof exists. The receipt layer
answers how that proof is produced, preserved, expired, and invalidated. A
receipt is not a promise and a template is not evidence.

## Evidence flow

```text
collector or isolated drill
        |
        v
observation JSON + non-secret artifacts
        |
        v
immutable prepende-recovery-receipt-v1
        |
        v
newest valid receipt selected for each gate
        |
        v
prepende-recovery-manifest-v1
        |
        v
Fast Continuity recoveryProven verdict
```

The default private runtime paths are:

```text
.engram/continuity/recovery-receipts/
.engram/continuity/recovery-manifest.json
```

They are runtime evidence, not Git-tracked source and not durable Prepende
memory. Preserve the receipt directory with the recovery evidence system; do
not copy it into a customer-safe clone.

## Gate contracts

| Gate | Required proof | Controlled run |
| --- | --- | --- |
| `inventory` | Complete asset, owner, dependency, RPO, and RTO inventory | Compare the reviewed inventory against Git, local services, Netlify, Supabase, assistant configuration, schedules, and credential owners. |
| `source_recovery` | Off-device fresh-clone restore | Clone the private remote into a clean temporary directory, verify the expected revision, then build or smoke-test it. A same-disk bundle alone does not pass. |
| `work_in_progress_recovery` | Off-device uncommitted-state restore | Restore a captured dirty-tree snapshot into a clean workspace and compare the reconstructed diff. A Git push does not cover uncommitted work. |
| `prepende_recovery` | Brain restore drill | Restore the vault and memory database in a temporary directory, compare counts, rebuild lexical RAG, and answer known queries. The existing restore-drill collector can import this result. |
| `assistant_continuity` | Cockpit/configuration restore | Restore reviewed Codex/Claude configuration without secrets, restore MCP registration, and prove a scoped Prepende handoff from the rebuilt cockpit. |
| `netlify_recovery` | Isolated Netlify rebuild | Recreate or deploy to a non-production site from the expected source revision, apply only a masked environment-key inventory through the owner-controlled secret path, and pass route/function checks. |
| `supabase_recovery` | Isolated database restore | Restore schema and data into an isolated project or approved local target, then verify migrations, RLS, Auth assumptions, and bounded row-count/checksum parity. Project health or migration listing alone does not pass. |
| `credential_recovery` | Owner-controlled recovery exercise | Verify the credential inventory, recovery-code or owner path, and rotation procedure without placing a secret, token, value, or recovery code in any receipt. |
| `lost_machine_drill` | Replacement-host acceptance | Starting from an isolated or replacement host, restore source and Prepende, reconnect hosted services through owner-controlled credentials, and run the acceptance suite. |
| `failure_detection` | Alert canary | Induce safe backup and restore failures, prove detection, alert delivery, owner acknowledgement, and persistent receipt creation. |

Hosted automation may emit bounded candidate evidence, but provider job success
is not a passed recovery drill. A candidate cannot independently establish the
replacement host lifecycle, complete service reconnection, isolated database
restore, credential lifecycle, artifact readback, or owner recovery path, and
it must not invoke the recorder as passing evidence.

A passing lost-machine receipt requires an independently authenticated
collector to bind provider execution and artifact evidence to every terminal
check. Provider-specific workflow and evaluator instructions belong in the
installation's private operator runbook. Without that binding, terminal checks
remain failed or pending and the candidate is ineligible for a passing receipt.

## Producing a receipt

Generate the exact observation skeleton:

```bash
python3 scripts/prepende_recovery_receipts.py template --gate <gate-id> \
  > /private/path/<gate-id>-observation.json
```

The real collector or drill fills the required check results and lists local,
non-secret evidence artifacts. Then record it:

```bash
python3 scripts/prepende_recovery_receipts.py record \
  --input /private/path/<gate-id>-observation.json
```

The recorder computes artifact digests, derives pass/fail from the required
checks, rejects secret-shaped fields, and refuses to overwrite a non-identical
receipt. It does not trust a caller-supplied `status`.

For the existing local brain restore drill:

```bash
MODEL_PROVIDER=echo python3 scripts/restore_drill.py
python3 scripts/prepende_recovery_receipts.py collect-restore-drill \
  --scope example-company--research
```

The collector imports only the latest JSONL drill result and maps its backup,
memory, vault, and lexical-RAG checks to the `prepende_recovery` contract.

## Building and verifying the manifest

Preview selection without changing the runtime manifest:

```bash
python3 scripts/prepende_recovery_receipts.py build \
  --scope example-company--research --dry-run
```

When the selected receipts are correct:

```bash
python3 scripts/prepende_recovery_receipts.py build \
  --scope example-company--research
python3 scripts/verify_prepende_recovery.py \
  --json --scope example-company--research
npm run prepende -- context-fast "Recovery acceptance" \
  --json --scope example-company--research --profile recovery
```

The build command may return nonzero while still writing a truthful partial
manifest. That means one or more gates are failed or unknown, not that manifest
assembly malfunctioned.

Manifest selection is tenant-scoped. Receipts from another business are
reported as ignored and can neither satisfy nor poison the selected scope.
Use `record-gap` to replace an unknown with a fresh, evidence-bearing failure
when a bounded rehearsal cannot perform the required external, owner-controlled,
off-device, or replacement-host drill. A red receipt is current proof of a gap;
it is never promoted to a pass.

```bash
python3 scripts/prepende_recovery_receipts.py record-gap \
  --gate lost_machine_drill \
  --scope example-company--research \
  --summary "No replacement host was available for this rehearsal."
```

## Hard safety boundaries

- Never restore into the production Netlify site or production Supabase
  project for evidence collection.
- Never put keys, tokens, passwords, private keys, recovery codes, webhook
  URLs, database contents, or customer data into receipt JSON.
- A masked environment-key inventory lists names and ownership only.
- A deploy receipt is not a source backup. A database health check is not a
  restore receipt. A local archive on the lost machine is not off-device proof.
- External provider drills, credential rotation, and a replacement-machine run
  require explicit scoped authorization because they may create resources,
  incur cost, or affect accounts even when production is protected.
