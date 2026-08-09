# Prepende customer-safe clone process

This is the canonical process for creating a customer or isolated Prepende
installation. The trusted private source repository is not a customer
distribution artifact. A normal Git clone, repository archive, copied worktree,
or copied owner vault is never an acceptable substitute for this process.

## Safety contract

Every customer installation must start from a default-deny export of the
reviewed Git index and become a new private repository with its own identity and
state. The export must contain no source-repository history, owner vault,
runtime data, local environment, Graphify output, credentials, absolute machine
paths, or customer-specific identity.

The durable implementation of this contract is:

- policy: `prepende-export-manifest.json`;
- exact reviewed path/blob inventory: `prepende-export-reviewed-inventory.json`;
- exporter: `scripts/export_prepende_clone.py`;
- inventory maintenance check: `python3 scripts/update_prepende_export_inventory.py --check`;
- sanitized seed: `vault-template/`;
- verification: `npm run verify:prepende:clone` and the clone smoke tests.

If any required check fails, stop. Do not hand off the export and do not work
around the refusal by copying files manually.

The accepted runtime must also keep owner promotion/import operations outside
MCP: `memory_approve` and `ingest_knowledge` are not registered tools or
capabilities. A candidate may be staged for owner review, and MCP may durably
write memory only through a separately granted `remember` capability.
Candidate provenance is server-derived; undeclared tenant, scope, principal,
agent, approval-path, connector, or packet fields are rejected before any
candidate state can change.

## 1. Prepare trusted source

Work in the trusted private checkout. Review and stage or commit every intended
source change because the exporter reads the Git index, not arbitrary untracked
working-tree files.

```bash
git status --short
git diff --cached --check
python3 scripts/update_prepende_export_inventory.py --check
npm run verify:prepende:clone
```

Resolve unexpected changes before continuing. Never add an owner vault,
runtime directory, `.env`, secret, or customer identity merely to make the
export include it.

## 2. Export outside the private checkout

Choose a new, empty destination outside the trusted repository:

```bash
python3 scripts/export_prepende_clone.py \
  --output /absolute/path/to/prepende-customer-clean \
  --json
```

Accept the export only when the receipt reports all of the following:

- `ok: true`;
- `format: prepende-clean-source-v2`;
- `sourceSnapshot: git-index`;
- `historyIncluded: false`;
- `ownerVaultIncluded: false`;
- `runtimeStateIncluded: false`;
- `graphifyOutputIncluded: false`;
- `credentialsIncluded: false`;
- `operatorPathsIncluded: false`;
- `privacyScan.ok: true`;
- `privacyScan.policy: default-deny-v2`;
- 64-character `inventorySha256`, `reviewedInventorySha256`, and
  `sourceTreeSha256` values;
- one mode and SHA-256 receipt for every exported source file.

The exporter refuses an existing destination, a destination inside the trusted
checkout, unsafe file modes or symlinks, any allowed-prefix file absent from the
exact inventory, any mode or blob mismatch, binary/NUL/non-UTF-8 content, and
secret-, PII-, identity-, machine-, or prompt-injection-shaped content. Privacy
errors identify only a category and file path; they never echo matched content.

Historical v1 receipts remain evidence for the export they recorded, but only
the v2 inventory-bound contract qualifies a new customer-safe source release.

## 3. Create the isolated private repository

The launch verifier exercises the exporter recursively, so the history-free
tree must first have its own Git index and `HEAD`. Initialize and commit the
exact exported source before creating runtime state:

```bash
cd /absolute/path/to/prepende-customer-clean
git init
git add .
git commit -m "Initialize isolated Prepende installation"
```

This is a new private repository. It has no relationship to the trusted source
history; its first commit contains only the accepted clean-export tree and
`PREPENDE_CLONE_MANIFEST.json` receipt.

## 4. Bootstrap the isolated installation

Inside the exported directory, create a new environment and initialize only the
sanitized vault template:

```bash
cd /absolute/path/to/prepende-customer-clean
npm run bootstrap:prepende
install -m 600 .env.example .env

./bin/prepende init --data-dir ./prepende-data/default
./bin/prepende knowledge rebuild
./bin/prepende knowledge status --json
./bin/prepende knowledge search "bootstrap verification" --json
npm run verify:prepende:launch
```

Read `docs/PREPENDE_MEMORY_RUNTIME.md` before configuring the clone's memory
lane. Its zero-credential acceptance baseline is lexical RAG; embeddings,
Graphify, Obsidian, and memory graphics are separate optional decisions, not
launch requirements.

The bootstrap command requires an installed Python 3.11 or newer (and selects
one explicitly when the system `python3` is older), creates the clone-owned
`.venv`, installs the export's hash-locked MCP/TUI dependencies with hash
enforcement, and verifies those imports from that environment. It must not
point at or wrap an interpreter environment from the trusted source checkout
or silently resolve newer dependency versions.

The zero-credential default is the `echo` generation provider plus lexical
RAG. Provider accounts, embeddings, connectors, email, billing, domains, and
deployment are separate, explicitly configured gates.

Keep the new repository private. Give the installation unique tenant and
workspace identifiers, credentials, vault content, deployment targets, and
approval policies. Never reuse owner or another customer's runtime state.

## Handoff receipt

Record the export receipt, exported source revision/index tree, verification
commands, and pass/fail results with the customer installation. Do not include
secrets in the receipt. A website preview or successful source export is not
proof that paid models, hosted connectors, DNS, forms, email, or production
deployment are live.

## Non-negotiable rule

When someone asks to "clone Prepende," determine whether they mean an internal
trusted-development clone or a customer/isolated installation. Internal
developers with explicit authorization may clone the private source repository.
Every customer or isolated installation must use this runbook and the exporter.
