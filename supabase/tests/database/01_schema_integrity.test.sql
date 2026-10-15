-- Schema shape and integrity rules the backend/LLM worker depend on.
-- Run with: supabase test db
begin;
create extension if not exists pgtap with schema extensions;
select plan(33);

-- ---------------------------------------------------------------------
-- Every table/column the backend reads or writes exists
-- (backend/db/repository.py, maps/routes.py, routes/internal.py,
--  routes/users.py, services/runner.py)
-- ---------------------------------------------------------------------
select is(
    array(
        select e.tbl || '.' || e.col
        from (values
            ('profiles','id'),('profiles','name'),('profiles','created_at'),('profiles','updated_at'),
            ('mind_maps','id'),('mind_maps','user_id'),('mind_maps','title'),('mind_maps','description'),
            ('mind_maps','is_public'),('mind_maps','created_at'),('mind_maps','updated_at'),
            ('chat_threads','id'),('chat_threads','map_id'),('chat_threads','user_id'),('chat_threads','created_at'),
            ('chat_messages','id'),('chat_messages','thread_id'),('chat_messages','role'),
            ('chat_messages','content'),('chat_messages','created_at'),
            ('generation_jobs','id'),('generation_jobs','map_id'),('generation_jobs','triggered_by_message_id'),
            ('generation_jobs','job_type'),('generation_jobs','target_node_id'),('generation_jobs','status'),
            ('generation_jobs','progress_percent'),('generation_jobs','error_message'),('generation_jobs','created_at'),
            ('generation_jobs','started_at'),('generation_jobs','completed_at'),('generation_jobs','callback_token_hash'),
            ('seed_assets','id'),('seed_assets','generation_job_id'),('seed_assets','asset_type'),
            ('seed_assets','file_name'),('seed_assets','storage_key'),('seed_assets','mime_type'),
            ('seed_assets','content_text'),('seed_assets','file_size_bytes'),('seed_assets','checksum_sha256'),
            ('seed_assets','uploaded_at'),
            ('nodes','id'),('nodes','map_id'),('nodes','label'),('nodes','description'),('nodes','pos_x'),
            ('nodes','pos_y'),('nodes','background_color'),('nodes','text_color'),('nodes','width'),
            ('nodes','height'),('nodes','node_type'),('nodes','source_job_id'),('nodes','node_data'),
            ('nodes','created_at'),('nodes','updated_at'),
            ('edges','id'),('edges','map_id'),('edges','source_node_id'),('edges','target_node_id'),
            ('edges','label'),('edges','edge_data'),('edges','created_at'),
            ('embeddings','id'),('embeddings','generation_job_id'),('embeddings','map_id'),
            ('embeddings','chunk_text'),('embeddings','pinecone_vector_id'),('embeddings','embedding_model'),
            ('embeddings','source'),('embeddings','source_title'),('embeddings','source_url'),
            ('embeddings','retrieved_at'),('embeddings','created_at')
        ) as e(tbl, col)
        where not exists (
            select 1 from information_schema.columns c
            where c.table_schema = 'public' and c.table_name = e.tbl and c.column_name = e.col
        )
    ),
    '{}'::text[],
    'every column referenced by the backend exists (lists any that are missing)'
);

select col_type_is('public', 'generation_jobs', 'callback_token_hash', 'character varying(64)',
    'callback_token_hash fits a SHA-256 hex digest');
select col_type_is('public', 'embeddings', 'pinecone_vector_id', 'character varying(100)',
    'pinecone_vector_id is varchar(100)');
select col_type_is('public', 'generation_jobs', 'status', 'text',
    'generation_jobs.status is text, so internal.report_fail can reuse $2 as value and in CASE');
select col_type_is('public', 'nodes', 'node_data', 'jsonb', 'nodes.node_data is jsonb (asyncpg jsonb codec)');
select col_type_is('public', 'edges', 'edge_data', 'jsonb', 'edges.edge_data is jsonb (asyncpg jsonb codec)');
select hasnt_table('public', 'users', 'the old public.users table is gone (renamed to profiles)');

-- ---------------------------------------------------------------------
-- Indexes, functions, storage
-- ---------------------------------------------------------------------
select has_index('public', 'mind_maps', 'idx_mind_maps_user_id_updated',
    'GET /api/maps (user_id, updated_at DESC) is indexed');
select has_index('public', 'generation_jobs', 'idx_generation_jobs_map_id_created',
    'latest-job lookup (map_id, created_at DESC) is indexed');
select has_index('public', 'generation_jobs', 'idx_generation_jobs_triggered_by_message_id',
    'generation_jobs.triggered_by_message_id FK is indexed');
select has_index('public', 'embeddings', 'idx_embeddings_pinecone_vector_id',
    'embeddings.pinecone_vector_id is indexed');
select hasnt_function('public', 'match_embeddings',
    'the legacy pgvector RPC match_embeddings() has been removed');

select is(
    array(
        select p.proname::text from pg_proc p
        where p.pronamespace = 'public'::regnamespace
          and p.prolang = (select oid from pg_language where lanname = 'plpgsql')
          and not exists (select 1 from unnest(coalesce(p.proconfig, '{}')) cfg where cfg like 'search_path=%')
        order by 1
    ),
    '{}'::text[],
    'every plpgsql function in public pins its search_path'
);

select results_eq(
    $$select public, file_size_limit, allowed_mime_types from storage.buckets where id = 'seed-assets'$$,
    $$values (false, 26214400::bigint,
              array['application/pdf','image/png','image/jpeg','image/webp']::text[])$$,
    'seed-assets bucket is private, 25 MB, PDF/PNG/JPEG/WEBP (matches backend/uploads/service.py)'
);

-- ---------------------------------------------------------------------
-- Fixtures
-- ---------------------------------------------------------------------
insert into auth.users (id, email, raw_user_meta_data) values
    ('a0000000-0000-0000-0000-000000000001', 'alice@example.com', '{"full_name":"Alice A"}'),
    ('a0000000-0000-0000-0000-000000000002', 'bob@example.com',   '{}');

insert into public.mind_maps (id, user_id) values
    ('b0000000-0000-0000-0000-000000000001', 'a0000000-0000-0000-0000-000000000001'),
    ('b0000000-0000-0000-0000-000000000002', 'a0000000-0000-0000-0000-000000000001');

insert into public.nodes (id, map_id, label) values
    ('c0000000-0000-0000-0000-000000000001', 'b0000000-0000-0000-0000-000000000001', 'root'),
    ('c0000000-0000-0000-0000-000000000002', 'b0000000-0000-0000-0000-000000000001', 'child'),
    ('c0000000-0000-0000-0000-000000000003', 'b0000000-0000-0000-0000-000000000002', 'other map');

insert into public.edges (map_id, source_node_id, target_node_id)
values ('b0000000-0000-0000-0000-000000000001',
        'c0000000-0000-0000-0000-000000000001',
        'c0000000-0000-0000-0000-000000000002');

-- ---------------------------------------------------------------------
-- Auth integration
-- ---------------------------------------------------------------------
select is(
    (select name from public.profiles where id = 'a0000000-0000-0000-0000-000000000001'),
    'Alice A',
    'signing up creates a profile row with the display name from user metadata'
);
select ok(
    exists (select 1 from public.profiles where id = 'a0000000-0000-0000-0000-000000000002' and name is null),
    'a profile is created even without a name in metadata'
);

-- ---------------------------------------------------------------------
-- Graph integrity
-- ---------------------------------------------------------------------
select throws_ok(
    $$insert into public.edges (map_id, source_node_id, target_node_id)
      values ('b0000000-0000-0000-0000-000000000001',
              'c0000000-0000-0000-0000-000000000001',
              'c0000000-0000-0000-0000-000000000003')$$,
    'P0001', null,
    'an edge pointing at a node from another map is rejected'
);
select throws_ok(
    $$insert into public.edges (map_id, source_node_id, target_node_id)
      values ('b0000000-0000-0000-0000-000000000001',
              'c0000000-0000-0000-0000-000000000001',
              'c0000000-0000-0000-0000-000000000002')$$,
    '23505', null,
    'a duplicate unlabeled edge is rejected'
);
select throws_ok(
    $$insert into public.edges (map_id, source_node_id, target_node_id)
      values ('b0000000-0000-0000-0000-000000000001',
              'c0000000-0000-0000-0000-000000000001',
              'c0000000-0000-0000-0000-000000000001')$$,
    '23514', null,
    'a self-loop edge is rejected'
);
select throws_ok(
    $$insert into public.nodes (map_id, label, background_color)
      values ('b0000000-0000-0000-0000-000000000001', 'bad colour', 'red')$$,
    '23514', null,
    'node colours must be #RRGGBB (same rule as llm/validation/schema.py)'
);

-- ---------------------------------------------------------------------
-- Generation jobs
-- ---------------------------------------------------------------------
insert into public.generation_jobs (id, map_id, job_type)
values ('d0000000-0000-0000-0000-000000000001', 'b0000000-0000-0000-0000-000000000001', 'generate');

select throws_ok(
    $$insert into public.generation_jobs (map_id, job_type)
      values ('b0000000-0000-0000-0000-000000000001', 'regenerate_full')$$,
    '23505', null,
    'a second active full-map job on the same map is rejected (repository.ActiveJobAlreadyExists)'
);
select lives_ok(
    $$insert into public.generation_jobs (map_id, job_type, target_node_id)
      values ('b0000000-0000-0000-0000-000000000001', 'regenerate_node',
              'c0000000-0000-0000-0000-000000000002')$$,
    'node-level regeneration can run alongside a full-map job'
);
select throws_ok(
    $$update public.generation_jobs set status = 'completed', started_at = now(), completed_at = now(),
             progress_percent = 50
      where id = 'd0000000-0000-0000-0000-000000000001'$$,
    '23514', null,
    'the job lifecycle CHECK rejects a completed job that is not at 100%'
);
select lives_ok(
    $$delete from public.nodes where id = 'c0000000-0000-0000-0000-000000000002'$$,
    'deleting a node targeted by a regeneration job succeeds (target is cleared)'
);

-- ---------------------------------------------------------------------
-- Seed assets (llm/contracts/models.py SeedAssetDraft)
-- ---------------------------------------------------------------------
select lives_ok(
    $$insert into public.seed_assets (generation_job_id, asset_type, content_text, file_size_bytes)
      values ('d0000000-0000-0000-0000-000000000001', 'text', 'free text seed', 14)$$,
    'a text seed asset without a storage key is accepted'
);
select throws_ok(
    $$insert into public.seed_assets (generation_job_id, asset_type, file_name, content_text)
      values ('d0000000-0000-0000-0000-000000000001', 'pdf', 'a.pdf', 'x')$$,
    '23514', null,
    'a PDF seed asset must have a storage key'
);
select throws_ok(
    $$insert into public.seed_assets (generation_job_id, asset_type, content_text, checksum_sha256)
      values ('d0000000-0000-0000-0000-000000000001', 'text', 'x', 'not-a-hash')$$,
    '23514', null,
    'checksum_sha256 must be 64 hex characters'
);

-- ---------------------------------------------------------------------
-- Embeddings (Pinecone metadata only)
-- ---------------------------------------------------------------------
select lives_ok(
    $$insert into public.embeddings
        (generation_job_id, map_id, chunk_text, pinecone_vector_id, embedding_model, source)
      values ('d0000000-0000-0000-0000-000000000001', 'b0000000-0000-0000-0000-000000000001',
              'Pinecone-backed chunk', 'pinecone-test-vector-1', 'test-model', 'uploaded_seed')$$,
    'backend can store Pinecone chunk metadata without a local vector'
);
select throws_ok(
    $$insert into public.embeddings
        (generation_job_id, map_id, chunk_text, embedding_model, source)
      values ('d0000000-0000-0000-0000-000000000001', 'b0000000-0000-0000-0000-000000000001',
              'web chunk', 'test-model', 'web_search')$$,
    '23514', null,
    'web_search/arxiv chunks must carry a source_url'
);
select throws_ok(
    $$insert into public.embeddings
        (generation_job_id, map_id, chunk_text, embedding_model, source)
      values ('d0000000-0000-0000-0000-000000000001', 'b0000000-0000-0000-0000-000000000002',
              'wrong map', 'test-model', 'uploaded_seed')$$,
    'P0001', null,
    'an embedding cannot point at a job from another map'
);

-- ---------------------------------------------------------------------
-- Ownership
-- ---------------------------------------------------------------------
select throws_ok(
    $$update public.mind_maps set user_id = 'a0000000-0000-0000-0000-000000000002'
      where id = 'b0000000-0000-0000-0000-000000000001'$$,
    'P0001', null,
    'a mind map cannot change owner'
);
select throws_ok(
    $$insert into public.chat_threads (map_id, user_id)
      values ('b0000000-0000-0000-0000-000000000001', 'a0000000-0000-0000-0000-000000000002')$$,
    'P0001', null,
    'a chat thread must belong to the map owner'
);

-- ---------------------------------------------------------------------
-- Account deletion cascades through everything the user owns
-- ---------------------------------------------------------------------
delete from auth.users where id = 'a0000000-0000-0000-0000-000000000001';
select is(
    (select count(*)::int from public.mind_maps where user_id = 'a0000000-0000-0000-0000-000000000001')
  + (select count(*)::int from public.nodes   where map_id in ('b0000000-0000-0000-0000-000000000001','b0000000-0000-0000-0000-000000000002'))
  + (select count(*)::int from public.generation_jobs where map_id = 'b0000000-0000-0000-0000-000000000001')
  + (select count(*)::int from public.seed_assets)
  + (select count(*)::int from public.embeddings)
  + (select count(*)::int from public.profiles where id = 'a0000000-0000-0000-0000-000000000001'),
    0,
    'deleting the auth user removes the profile, maps, nodes, jobs, seed assets and embeddings'
);

select * from finish();
rollback;
