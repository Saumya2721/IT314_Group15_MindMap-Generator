-- =====================================================================
-- Map snapshots (version history) for US-04 / S2-FE7.
--
-- Adds public.map_snapshots so the frontend can list, preview and restore
-- past states of a mind map. Forward-only and non-destructive: no table,
-- column or row is dropped.
--
-- Source of truth used for every change below:
--   backend/db/repository.py, backend/maps/routes.py,
--   backend/app/routes/internal.py, llm/contracts/models.py
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. public.map_snapshots table
--    One row per generation job completion (initial generation,
--    full regen, branch regen). Ownership is inherited from mind_maps:
--    deleting a map cascades its snapshots away.
-- ---------------------------------------------------------------------
create table public.map_snapshots (
    id              uuid primary key default gen_random_uuid(),
    map_id          uuid not null references public.mind_maps(id) on delete cascade,
    version_number  int not null,
    trigger_source  text not null check (
        trigger_source in ('initial_generation', 'full_regen', 'branch_regen', 'manual')
    ),
    trigger_job_id  uuid references public.generation_jobs(id) on delete set null,
    label           varchar(255),
    snapshot_data   jsonb not null,
    created_by      uuid references public.profiles(id) on delete set null,
    created_at      timestamptz not null default now()
);

create index idx_map_snapshots_map_id
    on public.map_snapshots (map_id);

create unique index uq_map_snapshots_map_version
    on public.map_snapshots (map_id, version_number);

-- ---------------------------------------------------------------------
-- 2. Row Level Security (Supabase Data API users only; backend connects
--    as the table owner / service-role key and is unaffected).
-- ---------------------------------------------------------------------
alter table public.map_snapshots enable row level security;

create policy map_snapshots_select_own on public.map_snapshots
    for select to authenticated
    using (
        exists (
            select 1 from public.mind_maps m
            where m.id = map_id and m.user_id = auth.uid()
        )
    );

-- Users cannot insert/update/delete snapshots through the Data API;
-- the backend owns all writes via its own transactions.
drop policy if exists map_snapshots_insert_own on public.map_snapshots;
drop policy if exists map_snapshots_update_own on public.map_snapshots;
drop policy if exists map_snapshots_delete_own on public.map_snapshots;

-- ---------------------------------------------------------------------
-- 3. Helpful column comment
-- ---------------------------------------------------------------------
comment on column public.map_snapshots.trigger_source is
    'initial_generation | full_regen | branch_regen | manual';
comment on column public.map_snapshots.snapshot_data is
    '{nodes:[...], edges:[...]} copied from the completed generation job result';
comment on column public.map_snapshots.trigger_job_id is
    'generation_jobs row that produced this snapshot; null for manual snapshots';
