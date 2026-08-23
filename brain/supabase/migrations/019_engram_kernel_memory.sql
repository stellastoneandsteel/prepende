-- 019_engram_kernel_memory.sql
--
-- Kernel MemoryStore lane: the substrate's own tenant-scoped durable memory
-- (memory/postgres_store.py). This is the generic hosted-memory substrate.
--
-- DISTINCT from the consumer-app lanes and intentionally NOT reusing their
-- tables: public.memories (001) and public.engram_memories (004) are keyed by
-- auth.users user_id for any product web app. THIS lane is keyed by
-- `scope` — a tenant slug (for example, 'company-a' or 'research-lab') —
-- and is read/written ONLY by the Engram substrate over a direct Postgres
-- connection. No PostgREST path exists to these tables (no grants, see below).
--
-- ids are text (`mem_<hex12>`, generated app-side) — the same format the
-- sqlite store uses — so the sqlite -> postgres backfill
-- (scripts/backfill_memory_to_postgres.py) preserves every id and supersede
-- chain byte-for-byte.
--
-- TENANT ISOLATION (the point of this migration):
--   * RLS is ENABLED **and FORCED**, and the substrate connects as a
--     dedicated least-privilege role: Supabase's `postgres` role can carry
--     BYPASSRLS, so forced RLS does NOT bind the default direct connection.
--     The enforced lane is `engram_brain` (LOGIN, no BYPASSRLS, DML grants on
--     the kernel tables ONLY — created live as admin, password in .env only),
--     which DATABASE_URL points at. The store sets the transaction-local GUC
--     `app.engram_scope` on every operation; a query that forgets it sees
--     zero rows instead of every tenant's rows — verified live by
--     tests/smoke_memory_postgres.py section 6.
--   * The policy is transaction-pooler safe: set_config(..., true) is
--     transaction-local and the store wraps every statement in an explicit
--     transaction, so pgbouncer/Supavisor transaction mode cannot leak the
--     GUC across tenants.
--   * anon/authenticated get REVOKE ALL — these tables don't exist as far as
--     PostgREST clients are concerned. service_role retains BYPASSRLS by
--     Supabase design (used for admin/backfill only).
--
-- STATUS: authored in-repo; apply to Engram's OWN Supabase project (never the
-- another product project) once it exists.

create extension if not exists vector with schema extensions;

create table if not exists public.engram_kernel_memories (
  id            text primary key,
  scope         text not null,
  content       text not null,
  metadata      jsonb not null default '{}'::jsonb,
  kind          text not null default 'episodic'
                  check (kind in ('episodic', 'semantic', 'procedural')),
  status        text not null default 'active'
                  check (status in ('active', 'archived', 'deleted')),
  created_at    timestamptz not null default now(),
  updated_at    timestamptz,
  deleted_at    timestamptz,
  valid_from    timestamptz,
  superseded_by text references public.engram_kernel_memories(id) on delete set null,
  -- jsonb embedding: any dimension, exact parity with the sqlite store; this
  -- is what hybrid recall scores against.
  embedding     jsonb,
  -- pgvector mirror, maintained when the embedder emits 1536-dim vectors;
  -- powers ANN candidate pre-selection once a scope outgrows the Python scan.
  embedding_vec extensions.vector(1536)
);

create index if not exists engram_kernel_memories_scope_created_idx
  on public.engram_kernel_memories (scope, created_at desc);
-- The live set (what recall scans): not deleted, not superseded.
create index if not exists engram_kernel_memories_scope_live_idx
  on public.engram_kernel_memories (scope, created_at desc)
  where status != 'deleted' and superseded_by is null;
create index if not exists engram_kernel_memories_vec_hnsw_idx
  on public.engram_kernel_memories using hnsw (embedding_vec extensions.vector_cosine_ops);

create table if not exists public.engram_kernel_memory_edges (
  id         bigint generated always as identity primary key,
  scope      text not null,
  src        text not null references public.engram_kernel_memories(id) on delete cascade,
  dst        text not null references public.engram_kernel_memories(id) on delete cascade,
  relation   text not null,
  created_at timestamptz not null default now()
);

create index if not exists engram_kernel_memory_edges_scope_src_idx
  on public.engram_kernel_memory_edges (scope, src);

-- ---- isolation -------------------------------------------------------------

-- Unreachable from PostgREST: no API role may touch the kernel lane.
revoke all on public.engram_kernel_memories from anon, authenticated;
revoke all on public.engram_kernel_memory_edges from anon, authenticated;
revoke all on sequence public.engram_kernel_memory_edges_id_seq from anon, authenticated;

alter table public.engram_kernel_memories enable row level security;
alter table public.engram_kernel_memories force row level security;
alter table public.engram_kernel_memory_edges enable row level security;
alter table public.engram_kernel_memory_edges force row level security;

-- One policy, all roles (service_role bypasses via BYPASSRLS): a row is
-- visible/writable only inside a transaction whose app.engram_scope matches.
-- coalesce(_, '') makes "GUC unset" match nothing rather than erroring.
do $$
begin
  if not exists (
    select 1 from pg_policies
    where schemaname = 'public' and tablename = 'engram_kernel_memories'
      and policyname = 'Kernel memories isolated by tenant scope'
  ) then
    create policy "Kernel memories isolated by tenant scope"
      on public.engram_kernel_memories
      for all
      using (scope = coalesce(current_setting('app.engram_scope', true), ''))
      with check (scope = coalesce(current_setting('app.engram_scope', true), ''));
  end if;

  if not exists (
    select 1 from pg_policies
    where schemaname = 'public' and tablename = 'engram_kernel_memory_edges'
      and policyname = 'Kernel memory edges isolated by tenant scope'
  ) then
    create policy "Kernel memory edges isolated by tenant scope"
      on public.engram_kernel_memory_edges
      for all
      using (scope = coalesce(current_setting('app.engram_scope', true), ''))
      with check (scope = coalesce(current_setting('app.engram_scope', true), ''));
  end if;
end $$;
