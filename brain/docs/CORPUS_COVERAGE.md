# Scoped corpus coverage

`context-fast`, `operational-status`, and the private MCP runtime report a
read-only comparison through `knowledge.contextCoverage`. Scope resolution is
shared with retrieval: configured `MEMORY_SCOPE` owns `VAULT_PATH`; every other
scope uses `VAULT_PATH/tenants/<scope>` and its path-derived SQLite index.
An owner index override never applies to a tenant. The fast lane reads only
nonsecret path/scope dotenv settings, respects the launcher allowlist, never
mutates process configuration, and never imports the model/runtime composition.

`observedSha256` and `indexedSha256` hash sorted relative paths and content
hashes. Counts can coincide for different corpora; compare these digests within
the same verified scope. The comparison reports no file paths or source text.
Index freshness also checks size and modification time. Uncheckpointed WALs
remain unavailable rather than pretending an immutable read saw recent rows.

Configure `PREPENDE_CORPUS_MANIFEST` as an owner-controlled local file path.
Include that key in an existing narrow dotenv allowlist if loading it from
`.env`. Do not broaden a launcher to load unrelated credentials. Neither an MCP
request nor retrieved content may select this file. Example manifest shape:

```json
{
  "schemaVersion": "prepende-corpus-manifest-v1",
  "scope": "your-verified-scope",
  "revision": "reviewed-corpus-v1",
  "approvalRef": "reference-to-the-real-owner-approval",
  "sources": [
    {"path": "wiki/reference.md", "sha256": "exact-64-character-content-digest", "purpose": "reference"},
    {"path": "raw/voice.md", "sha256": "exact-64-character-content-digest", "purpose": "voice"}
  ]
}
```

This shape example is not an approval. The source list must be independently
reviewed; generating a list of observed files does not authorize them. The
manifest's approval reference is an operator attestation, not a cryptographic
signature. Preserve its real approval receipt separately. The manifest is a
coverage check, not an ingest permission, retrieval authorization, publication
receipt, or grant to copy a corpus. Existing tenant and publication boundaries
continue to govern those actions.

Status distinguishes absent configuration, invalid manifests, scope mismatch,
content mismatch, and a matched manifest. Missing, changed, and unexpected
source counts explain drift. `voiceStatus` separately identifies missing pins
or changed pinned voice material. A configured mismatch blocks fast-context
continuity; no configured manifest preserves legacy index readiness while
explicitly showing that corpus authorization and voice pins remain unverified.
An MCP status receipt is descriptive; do not treat it as permission to publish.
