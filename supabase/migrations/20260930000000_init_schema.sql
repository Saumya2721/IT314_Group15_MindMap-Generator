-- =====================================================================
-- Mind Map Generator — initial schema (v3, Supabase edition)
-- Group 15, IT314 Software Engineering
--
-- Derived from schema v2 with these Supabase-specific changes:
--   1. Authentication is handled by Supabase Auth. public.users is now a
--      profile table keyed to auth.users(id); hashed_password, the OAuth
--      columns and the sessions table are gone.
--   2. Row Level Security is enabled on every table (PostgREST exposes
--      the public schema, so a table without RLS is readable by anyone
--      holding the anon key).
--   3. pgvector is installed in the "extensions" schema, as Supabase
--      recommends.
--   4. match_embeddings() is added because supabase-py cannot express
--      pgvector distance ordering directly; call it with .rpc().
--
-- The service_role key bypasses RLS. Keep it on the server only.
-- =====================================================================

create extension if not exists vector with schema extensions;

-- ---------------------------------------------------------------------
-- 0. Shared trigger: keep updated_at current
-- ---------------------------------------------------------------------
create or replace function public.set_updated_at()
returns trigger as $$
begin
    new.updated_at = now();
    return new;
end;
$$ language plpgsql;

-- ---------------------------------------------------------------------
-- 1. USERS (profile row per Supabase Auth user)
-- ---------------------------------------------------------------------
create table public.users (
    id          uuid primary key references auth.users (id) on delete cascade,
    email       varchar(255) not null,
    name        varchar(255),
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now()
);

create unique index uq_users_email_lower on public.users (lower(email));

create trigger trg_users_updated_at
    before update on public.users
    for each row execute function public.set_updated_at();

-- Create the profile row whenever someone signs up (email or Google).
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
    insert into public.users (id, email, name)
    values (
        new.id,
        new.email,
        coalesce(new.raw_user_meta_data ->> 'full_name', new.raw_user_meta_data ->> 'name')
    );
    return new;
end;
$$;

create trigger on_auth_user_created
    after insert on auth.users
    for each row execute function public.handle_new_user();

-- ---------------------------------------------------------------------
-- 2. MIND_MAPS
-- ---------------------------------------------------------------------
create table public.mind_maps (
    id          uuid primary key default gen_random_uuid(),
    user_id     uuid not null references public.users (id) on delete cascade,
    title       varchar(255) not null default 'Untitled mind map',
    description text,
    is_public   boolean not null default false,
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now()
);

create index idx_mind_maps_user_id_created on public.mind_maps (user_id, created_at desc);

create trigger trg_mind_maps_updated_at
    before update on public.mind_maps
    for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------------
-- 3. CHAT_THREADS (exactly one thread per map; thread id doubles as the
--    LangGraph thread_id; regenerate requests reuse it)
-- ---------------------------------------------------------------------
create table public.chat_threads (
    id          uuid primary key default gen_random_uuid(),
    map_id      uuid not null unique references public.mind_maps (id) on delete cascade,
    user_id     uuid not null references public.users (id) on delete cascade,
    created_at  timestamptz not null default now()
);

create index idx_chat_threads_user_id on public.chat_threads (user_id);

-- ---------------------------------------------------------------------
-- 4. CHAT_MESSAGES
-- ---------------------------------------------------------------------
create table public.chat_messages (
    id          uuid primary key default gen_random_uuid(),
    thread_id   uuid not null references public.chat_threads (id) on delete cascade,
    role        varchar(20) not null check (role in ('user', 'assistant')),
    content     text not null,
    created_at  timestamptz not null default now()
);

create index idx_chat_messages_thread_id_created on public.chat_messages (thread_id, created_at);

-- ---------------------------------------------------------------------
-- 5. GENERATION_JOBS (async task tracking only)
-- ---------------------------------------------------------------------
create table public.generation_jobs (
    id                      uuid primary key default gen_random_uuid(),
    map_id                  uuid not null references public.mind_maps (id) on delete cascade,
    triggered_by_message_id uuid references public.chat_messages (id) on delete set null,

    job_type          varchar(20) not null default 'generate'
                      check (job_type in ('generate', 'regenerate_full', 'regenerate_node')),
    target_node_id    uuid,   -- FK to nodes(id) added below, after nodes exists

    status            varchar(20) not null default 'pending'
                      check (status in ('pending', 'running', 'completed', 'failed', 'rejected')),
    progress_percent  int not null default 0 check (progress_percent between 0 and 100),
    error_message     text,

    created_at        timestamptz not null default now(),
    started_at        timestamptz,
    completed_at      timestamptz,

    constraint chk_target_node_matches_job_type check (
        (job_type = 'regenerate_node' and target_node_id is not null) or
        (job_type <> 'regenerate_node' and target_node_id is null)
    ),

    -- pending   -> nothing has happened yet
    -- running   -> started, not finished, 0-99%, no error
    -- completed -> started, finished, 100%, no error
    -- failed    -> started, then broke partway through
    -- rejected  -> never started (rejected at intake)
    constraint chk_generation_job_lifecycle check (
        (status = 'pending'
            and started_at is null and completed_at is null
            and progress_percent = 0 and error_message is null)
        or (status = 'running'
            and started_at is not null and completed_at is null
            and progress_percent between 0 and 99 and error_message is null)
        or (status = 'completed'
            and started_at is not null and completed_at is not null
            and progress_percent = 100 and error_message is null)
        or (status = 'failed'
            and started_at is not null and completed_at is not null
            and progress_percent between 0 and 99 and error_message is not null)
        or (status = 'rejected'
            and started_at is null and completed_at is not null
            and progress_percent = 0 and error_message is not null)
    ),

    constraint chk_generation_timestamps check (
        (started_at is null or started_at >= created_at) and
        (completed_at is null or completed_at >= created_at) and
        (completed_at is null or started_at is null or completed_at >= started_at)
    )
);

create index idx_generation_jobs_map_id          on public.generation_jobs (map_id);
create index idx_generation_jobs_status_created  on public.generation_jobs (status, created_at);
create index idx_generation_jobs_target_node_id  on public.generation_jobs (target_node_id);

-- At most one active full-map generation per map; node-level regens may
-- run concurrently for different nodes.
create unique index uq_generation_jobs_active_full_map
    on public.generation_jobs (map_id)
    where status in ('pending', 'running') and job_type in ('generate', 'regenerate_full');

-- ---------------------------------------------------------------------
-- 6. SEED_ASSETS (binary files live in Supabase Storage; storage_key is
--    the object path inside the "seed-assets" bucket)
-- ---------------------------------------------------------------------
create table public.seed_assets (
    id                 uuid primary key default gen_random_uuid(),
    generation_job_id  uuid not null references public.generation_jobs (id) on delete cascade,
    asset_type         varchar(20) not null check (asset_type in ('pdf', 'image', 'text')),
    file_name          varchar(255),
    storage_key        text,
    mime_type          varchar(100),
    content_text       text,
    file_size_bytes    bigint,
    checksum_sha256    varchar(64),
    uploaded_at        timestamptz not null default now(),

    constraint chk_seed_asset_size check (file_size_bytes is null or file_size_bytes >= 0),
    constraint chk_seed_asset_storage check (
        (asset_type = 'text' and storage_key is null)
        or (asset_type in ('pdf', 'image') and storage_key is not null and file_name is not null)
    ),
    constraint chk_seed_asset_checksum check (
        checksum_sha256 is null or checksum_sha256 ~ '^[0-9A-Fa-f]{64}$'
    )
);

create index idx_seed_assets_job_id on public.seed_assets (generation_job_id);

-- ---------------------------------------------------------------------
-- 7. NODES
-- ---------------------------------------------------------------------
create table public.nodes (
    id                uuid primary key default gen_random_uuid(),
    map_id            uuid not null references public.mind_maps (id) on delete cascade,
    label             varchar(255) not null,
    description       text,
    pos_x             double precision not null default 0,
    pos_y             double precision not null default 0,
    background_color  varchar(7),
    text_color        varchar(7),
    width             double precision,
    height            double precision,
    node_type         varchar(50) not null default 'default',
    source_job_id     uuid references public.generation_jobs (id) on delete set null,
    node_data         jsonb,
    created_at        timestamptz not null default now(),
    updated_at        timestamptz not null default now(),

    constraint chk_nodes_dimensions check (
        (width is null or width > 0) and (height is null or height > 0)
    ),
    constraint chk_nodes_background_color check (
        background_color is null or background_color ~ '^#[0-9A-Fa-f]{6}$'
    ),
    constraint chk_nodes_text_color check (
        text_color is null or text_color ~ '^#[0-9A-Fa-f]{6}$'
    )
);

create index idx_nodes_map_id        on public.nodes (map_id);
create index idx_nodes_source_job_id on public.nodes (source_job_id);

create trigger trg_nodes_updated_at
    before update on public.nodes
    for each row execute function public.set_updated_at();

-- Circular reference with nodes.source_job_id, so this FK is added after
-- nodes exists.
alter table public.generation_jobs
    add constraint fk_generation_jobs_target_node
    foreign key (target_node_id) references public.nodes (id) on delete set null
    deferrable initially deferred;

-- ---------------------------------------------------------------------
-- 8. EDGES
-- ---------------------------------------------------------------------
create table public.edges (
    id              uuid primary key default gen_random_uuid(),
    map_id          uuid not null references public.mind_maps (id) on delete cascade,
    source_node_id  uuid not null references public.nodes (id) on delete cascade,
    target_node_id  uuid not null references public.nodes (id) on delete cascade,
    label           varchar(100),
    edge_data       jsonb,
    created_at      timestamptz not null default now(),

    constraint chk_edges_no_self_loop check (source_node_id <> target_node_id)
);

create index idx_edges_map_id         on public.edges (map_id);
create index idx_edges_source_node_id on public.edges (source_node_id);
create index idx_edges_target_node_id on public.edges (target_node_id);

-- NULL labels count as equal, so two identical unlabeled edges collide.
create unique index uq_edges_source_target_label
    on public.edges (source_node_id, target_node_id, label) nulls not distinct;

-- ---------------------------------------------------------------------
-- 9. EMBEDDINGS (RAG retrieval context only)
-- ---------------------------------------------------------------------
create table public.embeddings (
    id                 uuid primary key default gen_random_uuid(),
    generation_job_id  uuid not null references public.generation_jobs (id) on delete cascade,
    map_id             uuid not null references public.mind_maps (id) on delete cascade,
    chunk_text         text not null,
    embedding          vector(1536) not null,   -- must match the embedding model's dimension
    embedding_model    varchar(100) not null,
    source             varchar(20) not null check (source in ('uploaded_seed', 'web_search', 'arxiv')),
    source_title       text,
    source_url         text,
    retrieved_at       timestamptz,
    created_at         timestamptz not null default now(),

    constraint chk_embeddings_source_url check (
        (source in ('web_search', 'arxiv') and source_url is not null)
        or (source = 'uploaded_seed')
    )
);

create index idx_embeddings_job_id on public.embeddings (generation_job_id);
create index idx_embeddings_map_id on public.embeddings (map_id);

create index idx_embeddings_vector_hnsw
    on public.embeddings using hnsw (embedding vector_cosine_ops);

-- Nearest chunks for one generation job. Runs as the caller, so RLS applies.
create or replace function public.match_embeddings(
    query_embedding vector(1536),
    p_job_id        uuid,
    match_count     int default 8
)
returns table (
    id           uuid,
    chunk_text   text,
    source       varchar,
    source_title text,
    source_url   text,
    similarity   double precision
)
language sql
stable
set search_path = public, extensions
as $$
    select e.id, e.chunk_text, e.source, e.source_title, e.source_url,
           1 - (e.embedding <=> query_embedding)
    from public.embeddings e
    where e.generation_job_id = p_job_id
    order by e.embedding <=> query_embedding
    limit match_count;
$$;

-- ---------------------------------------------------------------------
-- 10. SAME-MAP INTEGRITY TRIGGERS
-- A single-column FK only proves the referenced row exists somewhere;
-- these prove it belongs to the same mind_map as the row being written.
-- ---------------------------------------------------------------------
create or replace function public.check_node_source_job_same_map()
returns trigger as $$
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
$$ language plpgsql;

create trigger trg_nodes_source_job_same_map
    before insert or update of source_job_id, map_id on public.nodes
    for each row execute function public.check_node_source_job_same_map();


create or replace function public.check_job_target_node_same_map()
returns trigger as $$
begin
    if new.target_node_id is not null then
        if not exists (
            select 1 from public.nodes
            where id = new.target_node_id and map_id = new.map_id
        ) then
            raise exception 'generation_job %: target_node_id % does not belong to map %',
                new.id, new.target_node_id, new.map_id;
        end if;
    end if;
    return new;
end;
$$ language plpgsql;

create trigger trg_generation_jobs_target_node_same_map
    before insert or update of target_node_id, map_id on public.generation_jobs
    for each row execute function public.check_job_target_node_same_map();


create or replace function public.check_edge_endpoints_same_map()
returns trigger as $$
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
$$ language plpgsql;

create trigger trg_edges_endpoints_same_map
    before insert or update of source_node_id, target_node_id, map_id on public.edges
    for each row execute function public.check_edge_endpoints_same_map();


create or replace function public.check_embedding_job_same_map()
returns trigger as $$
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
$$ language plpgsql;

create trigger trg_embeddings_job_same_map
    before insert or update of generation_job_id, map_id on public.embeddings
    for each row execute function public.check_embedding_job_same_map();


-- Belt and suspenders: chat_threads.map_id is UNIQUE, so a message's thread
-- already implies one map. Kept to catch application bugs.
create or replace function public.check_job_trigger_message_same_map()
returns trigger as $$
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
$$ language plpgsql;

create trigger trg_generation_jobs_trigger_message_same_map
    before insert or update of triggered_by_message_id, map_id on public.generation_jobs
    for each row execute function public.check_job_trigger_message_same_map();

-- ---------------------------------------------------------------------
-- 11. ROW LEVEL SECURITY
-- Ownership flows from mind_maps.user_id. Child-table policies select
-- from the parent table, which is itself filtered by RLS, so a row is
-- visible only when its whole ownership chain leads back to the caller.
-- No admin policies yet: the admin panel (US-11) should use the service
-- role from the backend and return aggregates only.
-- ---------------------------------------------------------------------
alter table public.users            enable row level security;
alter table public.mind_maps        enable row level security;
alter table public.chat_threads     enable row level security;
alter table public.chat_messages    enable row level security;
alter table public.generation_jobs  enable row level security;
alter table public.seed_assets      enable row level security;
alter table public.nodes            enable row level security;
alter table public.edges            enable row level security;
alter table public.embeddings       enable row level security;

create policy users_select_own on public.users
    for select to authenticated
    using (id = (select auth.uid()));

create policy users_update_own on public.users
    for update to authenticated
    using (id = (select auth.uid()))
    with check (id = (select auth.uid()));

create policy mind_maps_owner on public.mind_maps
    for all to authenticated
    using (user_id = (select auth.uid()))
    with check (user_id = (select auth.uid()));

create policy chat_threads_owner on public.chat_threads
    for all to authenticated
    using (user_id = (select auth.uid()) and map_id in (select id from public.mind_maps))
    with check (user_id = (select auth.uid()) and map_id in (select id from public.mind_maps));

create policy chat_messages_owner on public.chat_messages
    for all to authenticated
    using (thread_id in (select id from public.chat_threads))
    with check (thread_id in (select id from public.chat_threads));

create policy generation_jobs_owner on public.generation_jobs
    for all to authenticated
    using (map_id in (select id from public.mind_maps))
    with check (map_id in (select id from public.mind_maps));

create policy seed_assets_owner on public.seed_assets
    for all to authenticated
    using (generation_job_id in (select id from public.generation_jobs))
    with check (generation_job_id in (select id from public.generation_jobs));

create policy nodes_owner on public.nodes
    for all to authenticated
    using (map_id in (select id from public.mind_maps))
    with check (map_id in (select id from public.mind_maps));

create policy edges_owner on public.edges
    for all to authenticated
    using (map_id in (select id from public.mind_maps))
    with check (map_id in (select id from public.mind_maps));

create policy embeddings_owner on public.embeddings
    for all to authenticated
    using (map_id in (select id from public.mind_maps))
    with check (map_id in (select id from public.mind_maps));
