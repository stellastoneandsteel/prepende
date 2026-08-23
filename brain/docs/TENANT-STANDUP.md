# Tenant standup — one command after the infra exists

Stand up a tenant on a self-hosted Prepende brain so external clients (a website,
a chat, an MCP-capable assistant) can read/propose in that tenant's isolated
namespace over MCP. Generic by design: the scope slug is data, never in code.

There are exactly **two inherently-manual infra steps** (a script can't create a
cloud project or a host for you). Everything else is one command.

## The two manual steps

1. **Create the tenant's OWN Postgres.** A separate Supabase project (SEPARATION
   rule 3 — never a shared/product DB). Put its connection string in `.env` as
   `DATABASE_URL`. As the DB owner, create the least-privilege role and apply the
   kernel migrations using database-owner credentials:
   ```sql
   create role engram_brain login password '<from your password manager>';
   ```
   ```bash
   psql "$ADMIN_DATABASE_URL" -f supabase/migrations/019_engram_kernel_memory.sql
   psql "$ADMIN_DATABASE_URL" -f supabase/migrations/020_engram_kernel_queues.sql
   psql "$ADMIN_DATABASE_URL" -f supabase/migrations/021_kernel_scope_guards.sql
   # + 026/027 if the tenant will bring its own model
   ```
   Then point `.env` `DATABASE_URL` at `engram_brain` (not `postgres`).

2. **Deploy the MCP-HTTP cockpit.** The host external clients connect to, with
   `PREPENDE_MCP_TRANSPORT=http`, `PREPENDE_MCP_HOST=0.0.0.0`, and the minted
   token in `PREPENDE_TENANT_TOKENS` (legacy `ENGRAM_*` aliases remain accepted).
   HTTP bearer auth derives tenant, workspace, physical scope, and capabilities
   from the token on every request. Do not set a process-wide
   `PREPENDE_MCP_SCOPE` for a multi-tenant HTTP host; that pin is required only
   for one-scope stdio transport. Deploy `Dockerfile.mcp` on the private host of
   your choice; hosting credentials and source wiring remain operator-owned.

## The one command

```bash
scripts/standup_tenant.sh \
  --tenant <tenant-slug> \
  --workspace <workspace-slug> \
  --scope <tenant-slug>--<workspace-slug> \
  --pack packs/small-business.json \
  --backfill
```

Before any seed or state write it validates all three identities as lowercase
1–64 character slugs and recomputes the physical scope from both tenant and
workspace. A caller cannot select a different namespace; long identities use
the deterministic value printed by `namespace_for_identity`. Customer mode refuses a missing `DATABASE_URL`, a
non-Postgres URL, `MEMORY_BACKEND=sqlite`, or a conflicting `WORKSPACE_SCOPE`.
After preflight it forces `MEMORY_BACKEND=postgres`, so the memory factory fails
hard instead of taking its normal SQLite fallback. It checks required migration
files, seeds the tenant's operating discipline from the pack (`seed_tenant.py`),
optionally backfills existing SQLite memories into Postgres (`--backfill`),
mints a connector token (`mint_tenant_token.py`), and prints the connect env.
Applying and verifying migrations, the role, forced RLS, and database identity
on the target remain database-owner checks before handoff.

For an explicitly non-customer local fixture, SQLite is available only by
opt-in:

```bash
scripts/standup_tenant.sh --scope local-fixture --local-dev-sqlite
```

That mode forces `MEMORY_BACKEND=sqlite`, prints `NOT CUSTOMER-READY`, refuses
`--backfill`, and does not present deployment or handoff as completed.

Business facts are NOT seeded from the repo — they arrive through the intake
after seeding, recorded with their source. The pack carries only how the tenant
should behave.

## Connect a downstream client

The script prints these; set them where the client runs (e.g. Netlify):

```
ENGRAM_API_URL=https://<your-cockpit-host>/
ENGRAM_TENANT_TOKEN=<the minted token>
```

The token fixes the tenant, workspace, and physical scope server-side, so the
client can only act inside that namespace and can prove its paired identity to
an operator. Least privilege is the default (read + propose + chat + graph, no
durable writes or approvals); widen with `--capabilities` only if needed.

For a single-scope stdio installation, preflight the exact launch identity and
server-owned revision before starting the process:

```bash
./bin/prepende mcp stdio \
  --tenant example-company \
  --workspace example-company-sales \
  --scope example-company--example-company-sales \
  --deployment-revision release-1 \
  --capabilities safe \
  --preflight
```

Remove `--preflight` to start the server. `account` repeats the sanitized
`deploymentRevision`, a non-secret principal ID/fingerprint, and the exact
sorted capabilities effective for that connection. A downstream client should
compare all three with its pinned expectation before trusting the connection.
The reusable `.env.example` leaves MCP tenant, workspace, and scope blank so
bootstrap defaults cannot override this explicit identity.

## Verify

```bash
python3 tests/smoke_standup_tenant_preflight.py # backend + identity fail-closed checks
python3 tests/smoke_mint_tenant_token.py     # token shape + scope rules
python3 tests/smoke_mcp_auth_core.py         # token identity + capability contract
python3 tests/smoke_mcp_scope_isolation.py   # cross-tenant isolation
python3 tests/smoke_knowledge_scoped.py      # per-tenant vault namespace
```
