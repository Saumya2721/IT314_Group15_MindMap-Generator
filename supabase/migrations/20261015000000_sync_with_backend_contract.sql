-- =====================================================================
-- Sync the database with the current backend + LLM worker implementation.
--
-- Forward-only and NON-DESTRUCTIVE: no table, column or row is dropped.
-- Safe to apply on:
--   * a fresh database, after the four earlier migrations; and
--   * an existing database that is already populated, whether it was built
--     from the earlier migrations or by running backend/db/schema.sql by
--     hand (see supabase/README.md for the migration-history repair step
--     that the second case needs).
--
-- Source of truth used for every change below:
--   backend/db/repository.py, backend/maps/routes.py,
--   backend/routes/internal.py, backend/uploads/service.py,
--   backend/routes/users.py, llm/contracts/models.py
--
-- The legacy pgvector column public.embeddings.embedding is intentionally
-- kept (it may hold data in an existing project). Dropping it is a separate,
-- opt-in step: supabase/manual/drop_legacy_pgvector_column.sql
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. Storage: the "seed-assets" bucket must accept what the backend sends.
--    backend/uploads/service.py allows up to 25 MB per file
--    (MAX_FILE_BYTES = 25 * 1024 * 1024) and only the MIME types that
--    sniff_mime() can return. The earlier bucket limit of 20 MB made
--    20–25 MB uploads fail with a 502. The bucket stays private.
-- ---------------------------------------------------------------------
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
    'seed-assets',
    'seed-assets',
    false,
    26214400,
    array['application/pdf', 'image/png', 'image/jpeg', 'image/webp']
)
on conflict (id) do update
    set public             = false,
        file_size_limit    = excluded.file_size_limit,
        allowed_mime_types = excluded.allowed_mime_types;

-- Uploads/removals are done by the backend with the service-role key, so
-- users only ever need READ access to their own "<user_id>/..." folder.
-- The broad write policy from 20260930000100 was replaced by a read-only one
-- in 20261014000000; make sure it is gone and the read policy exists.
drop policy if exists seed_assets_storage_owner on storage.objects;
drop policy if exists seed_assets_storage_select_own on storage.objects;
create policy seed_assets_storage_select_own on storage.objects
    for select to authenticated
    using (
        bucket_id = 'seed-assets'
        and (storage.foldername(name))[1] = (select auth.uid())::text
    );

-- ---------------------------------------------------------------------
-- 2. Pin search_path on every trigger function (Supabase security advisor:
--    "function_search_path_mutable"). Bodies are unchanged and every
--    object reference is already schema-qualified.
-- ---------------------------------------------------------------------
create or replace function public.set_updated_at()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

create or replace function public.check_node_source_job_same_map()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    if new.source_job_id is not null then
        if not exists (
            select 1 from public.generation_jobs
            where id = new.source_job_id and map_id = new.map_id
        ) then
            raise exception 'node %: source_job_id % does not belong to map %',
                new.id, new.source_job_id, new.map_id;
        end if;
    end if;
    return new;
end;
$$;

create or replace function public.check_edge_endpoints_same_map()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    if not exists (
        select 1 from public.nodes where id = new.source_node_id and map_id = new.map_id
    ) then
        raise exception 'edge %: source_node_id % does not belong to map %',
            new.id, new.source_node_id, new.map_id;
    end if;
    if not exists (
        select 1 from public.nodes where id = new.target_node_id and map_id = new.map_id
    ) then
        raise exception 'edge %: target_node_id % does not belong to map %',
            new.id, new.target_node_id, new.map_id;
    end if;
    return new;
end;
$$;

create or replace function public.check_embedding_job_same_map()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    if not exists (
        select 1 from public.generation_jobs
        where id = new.generation_job_id and map_id = new.map_id
    ) then
        raise exception 'embedding %: generation_job_id % does not belong to map %',
            new.id, new.generation_job_id, new.map_id;
    end if;
    return new;
end;
$$;

create or replace function public.check_job_trigger_message_same_map()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    if new.triggered_by_message_id is not null then
        if not exists (
            select 1
            from public.chat_messages m
            join public.chat_threads t on t.id = m.thread_id
            where m.id = new.triggered_by_message_id and t.map_id = new.map_id
        ) then
            raise exception 'generation_job %: triggered_by_message_id % is not from a thread on map %',
                new.id, new.triggered_by_message_id, new.map_id;
        end if;
    end if;
    return new;
end;
$$;

-- ---------------------------------------------------------------------
-- 3. profiles: the table was renamed from "users", but its constraints
--    kept the old names. Rename them to match backend/db/schema.sql.
--    (Renames only; no data or behaviour changes.)
-- ---------------------------------------------------------------------
do $$
begin
    if exists (select 1 from pg_constraint
               where conrelid = 'public.profiles'::regclass and conname = 'users_pkey') then
        alter table public.profiles rename constraint users_pkey to profiles_pkey;
    end if;
    if exists (select 1 from pg_constraint
               where conrelid = 'public.profiles'::regclass and conname = 'users_id_fkey') then
        alter table public.profiles rename constraint users_id_fkey to profiles_id_fkey;
    end if;
end;
$$;

-- ---------------------------------------------------------------------
-- 4. Embeddings: vectors live in Pinecone (llm/rag/pinecone_store.py);
--    Postgres keeps chunk text + pinecone_vector_id for provenance
--    (backend/db/repository.py insert_embedding(s), get_map_embeddings).
--    Retire the unused pgvector retrieval path. Nothing calls
--    match_embeddings() (repository.hybrid_retrieve() raises
--    NotImplementedError), and as a public function it was an
--    unnecessary RPC endpoint.
-- ---------------------------------------------------------------------
-- Looked up by name so this also works on a database where the vector
-- extension was never installed (e.g. one built from backend/db/schema.sql).
do $$
declare
    f regprocedure;
begin
    for f in
        select p.oid::regprocedure
        from pg_proc p
        where p.proname = 'match_embeddings'
          and p.pronamespace = 'public'::regnamespace
    loop
        execute format('drop function %s', f);
    end loop;
end;
$$;
drop index if exists public.idx_embeddings_vector_hnsw;

create index if not exists idx_embeddings_pinecone_vector_id
    on public.embeddings (pinecone_vector_id);

do $$
begin
    if exists (select 1 from information_schema.columns
               where table_schema = 'public' and table_name = 'embeddings'
                 and column_name = 'embedding') then
        comment on column public.embeddings.embedding is
            'DEPRECATED: legacy pgvector(1536) column, unused since vectors moved to Pinecone. '
            'Always NULL for rows written by the current backend. Drop with '
            'supabase/manual/drop_legacy_pgvector_column.sql once you have confirmed it holds nothing you need.';
    end if;
end;
$$;

comment on column public.embeddings.pinecone_vector_id is
    'ID of the vector in the Pinecone index (namespace = map_id).';

-- ---------------------------------------------------------------------
-- 5. Indexes for the queries the backend actually runs.
-- ---------------------------------------------------------------------
-- GET /api/maps: WHERE user_id = $1 ORDER BY updated_at DESC
create index if not exists idx_mind_maps_user_id_updated
    on public.mind_maps (user_id, updated_at desc);

-- GET /api/maps/{id}/jobs/latest and has_active_job():
-- WHERE map_id = $1 ORDER BY created_at DESC LIMIT 1.
-- The composite index also serves plain map_id lookups and the
-- ON DELETE CASCADE from mind_maps, so the single-column one is redundant.
create index if not exists idx_generation_jobs_map_id_created
    on public.generation_jobs (map_id, created_at desc);
drop index if exists public.idx_generation_jobs_map_id;

-- Only foreign key without a supporting index (FK ON DELETE SET NULL is
-- checked for every chat message deleted when a map is deleted).
create index if not exists idx_generation_jobs_triggered_by_message_id
    on public.generation_jobs (triggered_by_message_id);

-- ---------------------------------------------------------------------
-- 6. generation_jobs.status: varchar(20) -> text.
--    backend/routes/internal.py report_fail() runs
--        SET status = $2, ... CASE WHEN $2 = 'rejected' ...
--    asyncpg lets Postgres infer parameter types; with a varchar column
--    Postgres deduces "character varying" from the assignment and "text"
--    from the comparison and refuses to prepare the statement
--    (42P08 "inconsistent types deduced for parameter $2"), so every
--    worker failure callback returned HTTP 500 and the job stayed
--    'running'. With a text column both deductions agree.
--    The allowed values are still enforced by the existing CHECK
--    constraint (pending/running/completed/failed/rejected), so the
--    length limit was redundant. varchar -> text is binary-compatible:
--    no data is changed.
-- ---------------------------------------------------------------------
alter table public.generation_jobs
    alter column status type text;

-- ---------------------------------------------------------------------
-- 7. generation_jobs.callback_token_hash is backend-only.
--    routes/internal.py authenticates worker callbacks against it. Map
--    owners can still read their own job status/progress through the Data
--    API (RLS policy generation_jobs_select_own), but not this column.
--    The backend (postgres role / service_role) is unaffected.
-- ---------------------------------------------------------------------
comment on column public.generation_jobs.callback_token_hash is
    'SHA-256 hex digest of the per-job worker callback bearer token. Backend-only; not readable by API users.';

revoke select on public.generation_jobs from anon, authenticated;
grant select (
    id, map_id, triggered_by_message_id, job_type, target_node_id, status,
    progress_percent, error_message, created_at, started_at, completed_at
) on public.generation_jobs to authenticated;
