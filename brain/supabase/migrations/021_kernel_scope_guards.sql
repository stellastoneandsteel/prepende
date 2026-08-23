-- 021_kernel_scope_guards.sql
--
-- Hardening from the gate-1 adversarial review of the kernel lane:
--
-- 1. EMPTY-SCOPE HOLE: the 019/020 policies used
--    coalesce(current_setting('app.engram_scope', true), '') with the intent
--    that "GUC unset matches nothing". Postgres subtlety: after a
--    transaction-local set_config ends, current_setting(...) on the same
--    pooled session returns '' (empty string, not NULL) — so a row written
--    with scope='' would be visible to every later GUC-less transaction on
--    that session. No such row exists (verified), but the hole must close
--    structurally: scope <> '' is now a CHECK on every kernel table AND part
--    of every policy. The store additionally rejects empty scopes app-side.
--
-- 2. ENGRAM_BRAIN IN-REPO: the substrate's least-privilege role (LOGIN, no
--    BYPASSRLS — required because Supabase's `postgres` role carries
--    BYPASSRLS, verified live 2026-06-11) previously existed only as
--    hand-applied state. Its grants are now recorded here, applied when the
--    role exists. Role creation itself stays manual (passwords never in
--    repo):  create role engram_brain login password '<from-password-manager>';

-- ---- 1. scope guards --------------------------------------------------------

alter table public.engram_kernel_memories
  add constraint engram_kernel_memories_scope_nonempty check (scope <> '') not valid;
alter table public.engram_kernel_memories
  validate constraint engram_kernel_memories_scope_nonempty;
alter table public.engram_kernel_memory_edges
  add constraint engram_kernel_memory_edges_scope_nonempty check (scope <> '') not valid;
alter table public.engram_kernel_memory_edges
  validate constraint engram_kernel_memory_edges_scope_nonempty;
alter table public.engram_kernel_approvals
  add constraint engram_kernel_approvals_scope_nonempty check (scope <> '') not valid;
alter table public.engram_kernel_approvals
  validate constraint engram_kernel_approvals_scope_nonempty;
alter table public.engram_kernel_memory_candidates
  add constraint engram_kernel_memory_candidates_scope_nonempty check (scope <> '') not valid;
alter table public.engram_kernel_memory_candidates
  validate constraint engram_kernel_memory_candidates_scope_nonempty;

do $$
declare
  t text;
  pol text;
begin
  for t, pol in
    select * from (values
      ('engram_kernel_memories', 'Kernel memories isolated by tenant scope'),
      ('engram_kernel_memory_edges', 'Kernel memory edges isolated by tenant scope'),
      ('engram_kernel_approvals', 'Kernel approvals isolated by tenant scope'),
      ('engram_kernel_memory_candidates', 'Kernel memory candidates isolated by tenant scope')
    ) as v(tbl, polname)
  loop
    execute format('drop policy if exists %I on public.%I', pol, t);
    execute format(
      'create policy %I on public.%I for all '
      'using (scope <> '''' and scope = coalesce(current_setting(''app.engram_scope'', true), '''')) '
      'with check (scope <> '''' and scope = coalesce(current_setting(''app.engram_scope'', true), ''''))',
      pol, t);
  end loop;
end $$;

-- ---- 2. engram_brain grants (applied when the role exists) ------------------

do $$
begin
  if exists (select 1 from pg_roles where rolname = 'engram_brain') then
    grant usage on schema public, extensions to engram_brain;
    grant select, insert, update, delete on
      public.engram_kernel_memories,
      public.engram_kernel_memory_edges,
      public.engram_kernel_approvals,
      public.engram_kernel_memory_candidates
      to engram_brain;
    grant usage on sequence public.engram_kernel_memory_edges_id_seq to engram_brain;
  end if;
end $$;
