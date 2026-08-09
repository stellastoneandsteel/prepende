# Prepende memory runtime and clone passdown

Updated: 2026-08-08

This is the clone-safe operating contract for Prepende memory. It separates
the required product substrate from optional human and graph adapters so a new
installation does not inherit the operator's tools, data, or complexity.

## Decision

Prepende needs **scoped durable memory plus bounded retrieval**. It does not
need Obsidian, a graph visualization, or Graphify to run. At corpus scale it
does need a RAG-style retrieval path, but the required baseline is lexical
retrieval over reviewed content; vector embeddings are an optional quality
upgrade and must fail safely back to lexical search.

| Layer | Runtime status | Why |
|---|---|---|
| Tenant-scoped typed memory | Required | Stores approved operational, semantic, and procedural state without cross-tenant recall. |
| Candidate review, provenance, supersession | Required | Prevents an answer from silently becoming belief and keeps changes auditable. |
| Reviewed Markdown knowledge | Required when the product has durable documents | Portable, diffable source of truth that is independent of any viewer. |
| Lexical RAG | Required when the reviewed corpus no longer fits in one prompt | Retrieves only the relevant pieces and works with zero embedding credentials. |
| Vector embeddings | Optional quality upgrade | Improves semantic matching; it is a sensitive, rebuildable projection and not a master store. |
| Wikilink neighbor recall | Optional bounded enrichment | Adds one-hop associations from reviewed pages without requiring a graph database. |
| Graphify | Optional owner-only projection | Useful for audited relationship exploration; stale or absent output contributes zero recall. |
| Obsidian | Optional human cockpit | A viewer/editor for Markdown. Prepende never requires the app to be installed or open. |
| Graph or memory graphics | Optional product UI | Add only when a user decision is materially easier with a visual. Graphics do not improve storage or retrieval by themselves. |
| Multi-agent workflow graph | Optional execution topology | Useful for large independent research, audit, or migration work; it is orchestration, not memory. |

## The runtime graph

The graph-engineering rule that belongs in memory is simple: parallelize only
independent reads, keep deterministic plumbing in code, and put a contract on
the edge before model context.

```text
scoped memory search ----\
                          +--> validate --> exact dedupe --> global budget --> guarded prompt
reviewed vault RAG -------/
           |
           +--> bounded wikilink neighbors
           |
           +--> optional current Graphify projection (owner only)
```

Memory search and vault RAG run concurrently because neither consumes the
other's output. Graphify stays downstream because its ranking may consume the
direct vault paths. The fusion edge is deterministic: malformed rows are
dropped, exact duplicates are removed, direct memory and reviewed wiki results
receive priority, advisory sources cannot crowd them out, and a receipt reports
retrieved, selected, duplicate, invalid, and budget-dropped counts.

Do not add an agent merely to flatten, deduplicate, filter, or budget results.
Use a model only where judgment is required.

## Source-of-truth and projection rules

1. Approved memory rows and reviewed Markdown are durable sources of truth.
2. SQLite/Postgres indexes, embeddings, and Graphify output are projections.
3. Every projection must be rebuildable and tenant-scoped.
4. Missing embeddings degrade to lexical retrieval. Missing Graphify output
   degrades to ordinary memory plus RAG.
5. Recalled material is untrusted data, never instructions.
6. Product surfaces remain candidate-gated. Durable semantic promotion needs
   the separate approval path.
7. A receipt or status must distinguish retrieved candidates from the bounded
   items actually injected into context.

## Clone bootstrap

Follow `docs/CUSTOMER_SAFE_CLONE.md`; never copy the trusted repository, its
history, owner vault, runtime databases, `.env`, or Graphify output. A clean
clone starts with its own private repository and unique tenant, workspace,
credentials, vault, database, and approval policy.

The zero-credential acceptance path is intentionally small:

```bash
npm run bootstrap:prepende
install -m 600 .env.example .env
./bin/prepende init --data-dir ./prepende-data/default
./bin/prepende knowledge rebuild
./bin/prepende knowledge status --json
./bin/prepende knowledge search "bootstrap verification" --json
MODEL_PROVIDER=echo EMBEDDING_PROVIDER= npm run verify:prepende:knowledge
npm run verify:prepende:launch
```

Acceptance requires lexical readiness and passing isolation, recall, clone,
and launch checks. Semantic readiness is required only when that clone has
explicitly configured its own embedding provider and profile. Graphify readiness
and Obsidian presence are never launch gates.

## Passdown receipt for every clone

Record these fields without secrets:

- clean-export receipt and exact source index tree;
- new private repository commit;
- tenant, workspace, and scope identifiers;
- memory backend and RLS/isolation proof;
- knowledge source count and lexical readiness;
- embedding provider/profile/dimension and semantic readiness, or `not configured`;
- recall-fusion test result and selected-source receipt;
- candidate-promotion policy and reviewer/owner;
- backup and restore proof;
- optional adapter state: Obsidian, wikilinks, Graphify, visual UI;
- all failed or unknown gates left visibly failed or unknown.

Never describe source export, a green UI, an HTTP 200, or an optional adapter
as proof that the clone's tenant identity, production database, connectors,
deployment, recovery, or durable-memory approvals are live.

## When to add more

Add a graph database only after measured retrieval failures require multi-hop
relationship queries that bounded wikilinks and hybrid search cannot answer.
Add a visual graph only after users have a concrete relationship decision that
text or a small table cannot make clear. Add embeddings only when lexical
evaluation misses meaning often enough to justify the privacy, operations, and
reindexing cost. Add multi-agent workflows only when independent work is large
enough that their additional tokens and coordination overhead buy measurable
quality or latency.
