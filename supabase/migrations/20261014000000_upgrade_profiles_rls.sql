-- Upgrade the existing v2 schema to the v3 profile/RLS model.
-- IMPORTANT: This is a forward migration. Do not edit the older migration files.

begin;

create schema if not exists private;

-- Supabase Auth owns identity/email; the application stores only profile data.
alter table public.users rename to profiles;
drop index if exists public.uq_users_email_lower;
alter table public.profiles drop column if exists email;
drop trigger if exists trg_users_updated_at on public.profiles;
create trigger trg_profiles_updated_at
    before update on public.profiles
    for each row execute function public.set_updated_at();

-- Update the auth trigger and create profile rows for users who already exist.
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
    insert into public.profiles (id, name)
    values (
        new.id,
        left(nullif(btrim(coalesce(
            new.raw_user_meta_data ->> 'full_name',
            new.raw_user_meta_data ->> 'name'
        )), ''), 255)
    )
    on conflict (id) do nothing;
    return new;
end;
$$;
revoke all on function public.handle_new_user() from public, anon, authenticated;

insert into public.profiles (id, name)
select u.id,
       left(nullif(btrim(coalesce(
           u.raw_user_meta_data ->> 'full_name',
           u.raw_user_meta_data ->> 'name'
       )), ''), 255)
from auth.users u
on conflict (id) do nothing;

-- Mind maps now default to the authenticated caller when created through the Data API.
alter table public.mind_maps alter column user_id set default auth.uid();

-- Prevent transferring ownership and ensure denormalized thread ownership matches its map.
create or replace function public.prevent_mind_map_owner_change()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    if new.user_id is distinct from old.user_id then
        raise exception 'mind_map %: owner (user_id) cannot be changed', old.id;
    end if;
    return new;
end;
$$;

drop trigger if exists trg_mind_maps_owner_immutable on public.mind_maps;
create trigger trg_mind_maps_owner_immutable
    before update of user_id on public.mind_maps
    for each row execute function public.prevent_mind_map_owner_change();

create or replace function public.check_chat_thread_owner_matches_map()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    if not exists (
        select 1 from public.mind_maps
        where id = new.map_id and user_id = new.user_id
    ) then
        raise exception 'chat_thread %: user_id % is not the owner of map %',
            new.id, new.user_id, new.map_id;
    end if;
    return new;
end;
$$;

drop trigger if exists trg_chat_threads_owner_matches_map on public.chat_threads;
create trigger trg_chat_threads_owner_matches_map
    before insert or update of user_id, map_id on public.chat_threads
    for each row execute function public.check_chat_thread_owner_matches_map();

-- A node may be deleted even if an old regeneration job targeted it.
alter table public.generation_jobs
    drop constraint if exists chk_target_node_matches_job_type;
alter table public.generation_jobs
    add constraint chk_target_node_matches_job_type
    check (job_type = 'regenerate_node' or target_node_id is null);

create or replace function public.check_job_target_node_same_map()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
    if tg_op = 'INSERT'
       and new.job_type = 'regenerate_node'
       and new.target_node_id is null then
        raise exception 'generation_job %: regenerate_node jobs require target_node_id', new.id;
    end if;

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
$$;

-- Add secure helper functions for ownership-based policies.
create or replace function private.is_map_owner(p_map_id uuid)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
    select exists (
        select 1 from public.mind_maps m
        where m.id = p_map_id and m.user_id = (select auth.uid())
    );
$$;

create or replace function private.is_public_map(p_map_id uuid)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
    select exists (
        select 1 from public.mind_maps m
        where m.id = p_map_id and m.is_public
    );
$$;

revoke all on schema private from public;
revoke all on all functions in schema private from public;
grant usage on schema private to anon, authenticated;
grant execute on function private.is_map_owner(uuid) to authenticated;
grant execute on function private.is_public_map(uuid) to anon, authenticated;

-- Remove the old broad policies before adding the narrower v3 policies.
drop policy if exists users_select_own on public.profiles;
drop policy if exists users_update_own on public.profiles;
drop policy if exists profiles_select_own on public.profiles;
drop policy if exists profiles_insert_own on public.profiles;
drop policy if exists profiles_update_own on public.profiles;

drop policy if exists mind_maps_owner on public.mind_maps;
drop policy if exists mind_maps_select_own on public.mind_maps;
drop policy if exists mind_maps_select_public on public.mind_maps;
drop policy if exists mind_maps_insert_own on public.mind_maps;
drop policy if exists mind_maps_update_own on public.mind_maps;
drop policy if exists mind_maps_delete_own on public.mind_maps;

drop policy if exists chat_threads_owner on public.chat_threads;
drop policy if exists chat_threads_select_own on public.chat_threads;
drop policy if exists chat_messages_owner on public.chat_messages;
drop policy if exists chat_messages_select_own on public.chat_messages;
drop policy if exists generation_jobs_owner on public.generation_jobs;
drop policy if exists generation_jobs_select_own on public.generation_jobs;
drop policy if exists seed_assets_owner on public.seed_assets;
drop policy if exists seed_assets_select_own on public.seed_assets;
drop policy if exists nodes_owner on public.nodes;
drop policy if exists nodes_select_own on public.nodes;
drop policy if exists nodes_select_public on public.nodes;
drop policy if exists nodes_insert_own on public.nodes;
drop policy if exists nodes_update_own on public.nodes;
drop policy if exists nodes_delete_own on public.nodes;
drop policy if exists edges_owner on public.edges;
drop policy if exists edges_select_own on public.edges;
drop policy if exists edges_select_public on public.edges;
drop policy if exists edges_insert_own on public.edges;
drop policy if exists edges_update_own on public.edges;
drop policy if exists edges_delete_own on public.edges;
drop policy if exists embeddings_owner on public.embeddings;
drop policy if exists embeddings_select_own on public.embeddings;
drop policy if exists seed_assets_storage_owner on storage.objects;
drop policy if exists seed_assets_storage_select_own on storage.objects;

-- Profiles: users may see/insert themselves and update only the display name.
create policy profiles_select_own on public.profiles
    for select to authenticated using (id = (select auth.uid()));
create policy profiles_insert_own on public.profiles
    for insert to authenticated with check (id = (select auth.uid()));
create policy profiles_update_own on public.profiles
    for update to authenticated
    using (id = (select auth.uid()))
    with check (id = (select auth.uid()));

-- Maps: private to the owner unless explicitly made public.
create policy mind_maps_select_own on public.mind_maps
    for select to authenticated using (user_id = (select auth.uid()));
create policy mind_maps_select_public on public.mind_maps
    for select to anon, authenticated using (is_public);
create policy mind_maps_insert_own on public.mind_maps
    for insert to authenticated with check (user_id = (select auth.uid()));
create policy mind_maps_update_own on public.mind_maps
    for update to authenticated
    using (user_id = (select auth.uid()))
    with check (user_id = (select auth.uid()));
create policy mind_maps_delete_own on public.mind_maps
    for delete to authenticated using (user_id = (select auth.uid()));

-- Nodes and edges can be edited by their map owner and read on public maps.
create policy nodes_select_own on public.nodes
    for select to authenticated using (private.is_map_owner(map_id));
create policy nodes_select_public on public.nodes
    for select to anon, authenticated using (private.is_public_map(map_id));
create policy nodes_insert_own on public.nodes
    for insert to authenticated with check (private.is_map_owner(map_id));
create policy nodes_update_own on public.nodes
    for update to authenticated
    using (private.is_map_owner(map_id))
    with check (private.is_map_owner(map_id));
create policy nodes_delete_own on public.nodes
    for delete to authenticated using (private.is_map_owner(map_id));

create policy edges_select_own on public.edges
    for select to authenticated using (private.is_map_owner(map_id));
create policy edges_select_public on public.edges
    for select to anon, authenticated using (private.is_public_map(map_id));
create policy edges_insert_own on public.edges
    for insert to authenticated with check (private.is_map_owner(map_id));
create policy edges_update_own on public.edges
    for update to authenticated
    using (private.is_map_owner(map_id))
    with check (private.is_map_owner(map_id));
create policy edges_delete_own on public.edges
    for delete to authenticated using (private.is_map_owner(map_id));

-- Chat, jobs, uploads, and embeddings are read-only to authenticated clients.
create policy chat_threads_select_own on public.chat_threads
    for select to authenticated using (private.is_map_owner(map_id));
create policy chat_messages_select_own on public.chat_messages
    for select to authenticated
    using (exists (
        select 1 from public.chat_threads t
        where t.id = chat_messages.thread_id and private.is_map_owner(t.map_id)
    ));
create policy generation_jobs_select_own on public.generation_jobs
    for select to authenticated using (private.is_map_owner(map_id));
create policy seed_assets_select_own on public.seed_assets
    for select to authenticated
    using (exists (
        select 1 from public.generation_jobs j
        where j.id = seed_assets.generation_job_id and private.is_map_owner(j.map_id)
    ));
create policy embeddings_select_own on public.embeddings
    for select to authenticated using (private.is_map_owner(map_id));

-- Limit grants as well as row-level policies.
revoke all on
    public.profiles, public.mind_maps, public.chat_threads, public.chat_messages,
    public.generation_jobs, public.seed_assets, public.nodes, public.edges, public.embeddings
from anon, authenticated;

grant select on public.mind_maps, public.nodes, public.edges to anon;
grant select, insert on public.profiles to authenticated;
grant update (name) on public.profiles to authenticated;
grant select, insert, delete on public.mind_maps to authenticated;
grant update (title, description, is_public) on public.mind_maps to authenticated;
grant select, insert, delete on public.nodes to authenticated;
grant update (label, description, pos_x, pos_y, background_color, text_color,
              width, height, node_type, node_data) on public.nodes to authenticated;
grant select, insert, delete on public.edges to authenticated;
grant update (source_node_id, target_node_id, label, edge_data) on public.edges to authenticated;
grant select on public.chat_threads, public.chat_messages, public.generation_jobs,
                public.seed_assets, public.embeddings to authenticated;
grant all on
    public.profiles, public.mind_maps, public.chat_threads, public.chat_messages,
    public.generation_jobs, public.seed_assets, public.nodes, public.edges, public.embeddings
to service_role;

-- Keep Storage private; authenticated users may read only their own folder.
create policy seed_assets_storage_select_own on storage.objects
    for select to authenticated
    using (
        bucket_id = 'seed-assets'
        and (storage.foldername(name))[1] = (select auth.uid())::text
    );

insert into storage.buckets (id, name, public)
values ('seed-assets', 'seed-assets', false)
on conflict (id) do nothing;

create schema if not exists langgraph;
revoke all on schema langgraph from public;
revoke all on schema langgraph from anon, authenticated;

commit;
