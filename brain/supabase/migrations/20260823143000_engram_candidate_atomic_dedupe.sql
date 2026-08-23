-- Atomic, backend-neutral deduplication for ingestion candidates. Only valid
-- metadata identities that name exactly one row in their tenant are backfilled.
-- Historical collisions stay NULL; no receipt is deleted, merged, or rewritten.

alter table public.engram_kernel_memory_candidates
  add column if not exists dedupe_key text;

with valid_metadata as (
  select id, scope, metadata->>'dedupe_key' as metadata_key
  from public.engram_kernel_memory_candidates
  where jsonb_typeof(metadata->'dedupe_key') = 'string'
    and metadata->>'dedupe_key' = btrim(metadata->>'dedupe_key')
    and char_length(metadata->>'dedupe_key') between 1 and 256
), identity_rows as (
  select id, scope, dedupe_key as identity_key
  from public.engram_kernel_memory_candidates
  where dedupe_key is not null
  union
  select id, scope, metadata_key as identity_key
  from valid_metadata
), unambiguous as (
  select scope, identity_key, min(id) as id
  from identity_rows
  group by scope, identity_key
  having count(distinct id) = 1
), backfill as (
  select valid_metadata.id, valid_metadata.scope, valid_metadata.metadata_key
  from valid_metadata
  join unambiguous
    on unambiguous.id = valid_metadata.id
   and unambiguous.scope = valid_metadata.scope
   and unambiguous.identity_key = valid_metadata.metadata_key
)
update public.engram_kernel_memory_candidates as candidate
set dedupe_key = backfill.metadata_key
from backfill
where candidate.id = backfill.id
  and candidate.scope = backfill.scope
  and candidate.dedupe_key is null;

create unique index if not exists engram_kernel_memory_candidates_scope_dedupe_key_idx
  on public.engram_kernel_memory_candidates (scope, dedupe_key)
  where dedupe_key is not null;
