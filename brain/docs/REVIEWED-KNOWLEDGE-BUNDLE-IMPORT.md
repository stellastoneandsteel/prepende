# Reviewed knowledge bundle import

Prepende accepts product knowledge through an owner-operated, fail-closed CLI.
The product runtime cannot call this importer. Its normal token should retain
only read/propose capabilities such as `knowledge_search`. `ingest_knowledge`
is not a registered MCP tool or capability, including for an `all` principal.

## Trust contract

An import requires two JSON files:

1. A graph-derived knowledge bundle containing a private tenant/workspace
   scope, `graphVersion`, documents, relationships, provenance, and each item's
   runtime-use contract.
2. A separately reviewed approval manifest that binds the exact bundle bytes
   by SHA-256 and names the exact approved item IDs.

The approval manifest has this shape:

```json
{
  "schema": "prepende.reviewed_knowledge_approval.v1",
  "bundleSchema": "your-product.graphify.knowledge_bundle.v1",
  "tenant": "example-company",
  "workspace": "example-company-sales",
  "graphVersion": "graph-2026-07-13",
  "bundleSha256": "<sha256-of-exact-bundle-file>",
  "approvedItemIds": ["doc-client-protection", "rel-protection-payment"],
  "approvedBy": "owner-review",
  "approvedAt": "2026-07-13T12:00:00Z"
}
```

Every approved bundle item must also carry:

- `approval.status: "source_policy_approved"`;
- provenance with the same `graphVersion` and a source document;
- `prepende.scope` (or the legacy `engram.scope`) matching the requested
  tenant and workspace;
- `prepende.physicalScope` matching the canonical namespace derived from that
  tenant and workspace (for example `example-company--example-company-sales`);
- `allowedRuntimeUse` containing only explicitly authorized uses, including
  `knowledge_search` for this importer;
- `memoryWrite: false`.

Approved relationships must connect two approved document graph nodes. Items
still present in `reviewQueue` are refused even if their IDs appear in the
approval manifest.

## Import

Run from the trusted Prepende host:

```bash
.venv/bin/python3 scripts/import_reviewed_knowledge_bundle.py \
  --bundle /reviewed/export/knowledge-bundle.json \
  --approval-manifest /reviewed/export/approval-manifest.json \
  --vault-base ./prepende-data/default/vault \
  --tenant example-company \
  --workspace example-company-sales
```

The physical vault scope is the canonical namespace derived from both tenant
and workspace. `--scope` may pin that expected value, but it cannot select a
different namespace. Every approved item's `physicalScope` must match it.

On success, the importer:

- writes deterministic provenance-rich Markdown under the scoped `wiki/`;
- creates Obsidian wikilinks for reviewed relationships and rebuilds the map of
  content;
- rebuilds that scope's disposable RAG projection;
- writes one content-addressed, read-only receipt under `receipts/` containing
  only fields that can be independently recomputed from the exact approved
  bundle, manifest, and rendered pages; and
- reports `operationCompletedAt` and `currentRagRebuild` in the CLI result as
  current operation facts, deliberately outside the immutable receipt.

Repeating the exact import verifies the pages and rebuilds RAG without
rewriting the receipt. A changed bundle hash, identity, graph version, item
set, page, or receipt is refused.

## Local default-deny screening

Upstream review never bypasses the importer’s own deterministic screen. Before
creating a scoped vault directory, lock, page, or RAG index, the importer scans
every key and scalar value in every approved document and relationship for bounded PII
(email, phone, SSN, and Luhn-valid payment-card shapes), secret shapes (private
keys, provider keys, bearer/JWT credentials, and credentialed URLs), and
prompt-injection markers. A match is refused without echoing the matched text.
There is no CLI bypass flag.

Before matching, screening canonicalizes each JSON scalar to temporary text
using Unicode NFKC and removes Unicode format controls. Full-width punctuation,
zero-width separators, and numeric card/phone values therefore cannot bypass a
detector merely because their original representation was not a plain string.
Error paths use structural ordinals rather than untrusted keys, so a refusal
does not copy matched content into logs. Safe numeric values keep their original
types; canonical text is used only by the gate.

The immutable receipt records the versioned screening policy,
canonicalization, passed status, categories, and deterministic
item/field/character counts. Replacing that receipt with a claimed bypass,
changing a count, or replaying legacy v1 evidence invalidates idempotent replay.

The persisted receipt does not claim that a local clock value or historical
RAG count is immutable: neither can be independently recovered later without
a signing service. Keep the approval manifest, bundle, vault, and receipt
directory owner-only. `--imported-at` fixes only the reported operation time,
which is useful for controlled test and audit output.

## Runtime identity

Mint a least-privilege token with distinct tenant and workspace receipts:

```bash
.venv/bin/python3 scripts/mint_tenant_token.py \
  --tenant example-company \
  --workspace example-company-sales \
  --scope example-company--example-company-sales
```

`account` then returns `tenant`, `tenantId`, `workspace`, `workspaceId`,
`scope`, a non-secret `principalId` and `principalFingerprint`, the exact
sorted `capabilities`, and the sanitized server-controlled
`deploymentRevision`. Legacy scope-only token entries still map all identity
fields to the original scope.

For rich identities, physical scope is not caller-chosen: it is derived from
both tenant and workspace. A mismatched scope is rejected before a vault path,
token, or import lock can be created.
