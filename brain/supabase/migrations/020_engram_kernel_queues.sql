-- 020_engram_kernel_queues.sql
--
-- Durable twins of the kernel's two review queues (W4 of
-- the per-action approval ledger
-- (kernel/core/approvals.py) and the memory candidate queue
-- (memory/candidates.py). These tables are the local-brain <-> production
-- bridge: a local brain and a hosted tenant cockpit read and
-- write the SAME rows once Engram's own Supabase project exists.
--
-- Same lane rules as 019_engram_kernel_memory.sql:
--   * scope-keyed (tenant slug), NOT auth.users-keyed — distinct from the
--     consumer-lane engram_memory_candidates (015), which is a different
--     product surface;
--   * ids are text in the kernel's formats (apr_<hex12>, cand_<hex16>) so a
--     sqlite -> postgres backfill preserves every id;
--   * RLS ENABLED + FORCED with the transaction-local app.engram_scope GUC;
--     anon/authenticated get REVOKE ALL (no PostgREST path); the Netlify
--     bridge uses service_role (BYPASSRLS) and scopes every query explicitly
--     by the spine-derived tenant slug.
--
-- STATUS: authored in-repo; apply to Engram's OWN Supabase project only
-- (never another product project).

-- ---- approvals: one row per staged external action -------------------------

create table if not exists public.engram_kernel_approvals (
  id           text primary key,
  scope        text not null,
  workflow     text not null,
  params       jsonb not null default '{}'::jsonb,
  reason       text not null default '',
  requested_by text not null default '',
  status       text not null default 'pending'
                 check (status in ('pending', 'approved', 'rejected', 'expired',
                                   'executed', 'execution_failed')),
  created_at   timestamptz not null default now(),
  expires_at   timestamptz,
  decided_by   text,
  decided_at   timestamptz,
  executed_at  timestamptz,
  result       jsonb
);

create index if not exists engram_kernel_approvals_scope_status_idx
  on public.engram_kernel_approvals (scope, status, created_at desc);

-- ---- memory candidates: the Assess gate's durable half ---------------------

create table if not exists public.engram_kernel_memory_candidates (
  id          text primary key,
  scope       text not null,
  kind        text not null default 'semantic'
                check (kind in ('episodic', 'semantic', 'procedural')),
  content     text not null,
  source      text not null default 'unknown',
  status      text not null default 'pending'
                check (status in ('pending', 'approved', 'rejected', 'redacted', 'deferred')),
  created_at  timestamptz not null default now(),
  reviewed_at timestamptz,
  memory_id   text references public.engram_kernel_memories(id) on delete set null,
  reason      text,
  metadata    jsonb not null default '{}'::jsonb
);

create index if not exists engram_kernel_memory_candidates_scope_status_idx
  on public.engram_kernel_memory_candidates (scope, status, created_at desc);

-- ---- isolation (mirror of 019) ----------------------------------------------

revoke all on public.engram_kernel_approvals from anon, authenticated;
revoke all on public.engram_kernel_memory_candidates from anon, authenticated;

alter table public.engram_kernel_approvals enable row level security;
alter table public.engram_kernel_approvals force row level security;
alter table public.engram_kernel_memory_candidates enable row level security;
alter table public.engram_kernel_memory_candidates force row level security;

do $$
begin
  if not exists (
    select 1 from pg_policies
    where schemaname = 'public' and tablename = 'engram_kernel_approvals'
      and policyname = 'Kernel approvals isolated by tenant scope'
  ) then
    create policy "Kernel approvals isolated by tenant scope"
      on public.engram_kernel_approvals
      for all
      using (scope = coalesce(current_setting('app.engram_scope', true), ''))
      with check (scope = coalesce(current_setting('app.engram_scope', true), ''));
  end if;

  if not exists (
    select 1 from pg_policies
    where schemaname = 'public' and tablename = 'engram_kernel_memory_candidates'
      and policyname = 'Kernel memory candidates isolated by tenant scope'
  ) then
    create policy "Kernel memory candidates isolated by tenant scope"
      on public.engram_kernel_memory_candidates
      for all
      using (scope = coalesce(current_setting('app.engram_scope', true), ''))
      with check (scope = coalesce(current_setting('app.engram_scope', true), ''));
  end if;
end $$;
