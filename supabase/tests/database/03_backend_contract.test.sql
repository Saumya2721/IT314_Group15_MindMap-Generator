-- Replays the exact SQL statements the FastAPI backend sends through asyncpg,
-- in the order of a real generate -> regenerate -> failure -> delete flow.
--
-- Statements are PREPAREd with untyped parameters, which is what asyncpg
-- does, so Postgres must infer every $n type exactly as it would in
-- production. Sources: backend/db/repository.py, backend/app/maps/routes.py,
-- backend/app/api/internal.py, backend/app/api/users.py, backend/app/services/runner.py.
-- Runs as the migration owner (like the backend's DATABASE_URL role), so
-- RLS does not apply here.
-- Run with: supabase test db
begin;
create extension if not exists pgtap with schema extensions;
select plan(58);

insert into auth.users (id, email, raw_user_meta_data)
values ('a0000000-0000-0000-0000-000000000001', 'alice@example.com', '{"name":"Alice"}');

-- =====================================================================
-- Prepared statements, copied verbatim from the backend
-- =====================================================================
select lives_ok($$prepare create_map as
    INSERT INTO public.mind_maps (user_id, title) VALUES ($1, $2)
    RETURNING id, title, is_public, created_at, updated_at$$, 'prepare maps.routes.create_map');
select lives_ok($$prepare list_maps as
    SELECT id, title, is_public, created_at, updated_at FROM public.mind_maps
    WHERE user_id = $1 ORDER BY updated_at DESC$$, 'prepare maps.routes.list_maps');
select lives_ok($$prepare assert_owner as
    SELECT 1 FROM public.mind_maps WHERE id = $1 AND user_id = $2$$, 'prepare repository.assert_map_owner');
select lives_ok($$prepare find_thread as
    SELECT id FROM public.chat_threads WHERE map_id = $1$$, 'prepare get_or_create_chat_thread (select)');
select lives_ok($$prepare create_thread as
    INSERT INTO public.chat_threads (map_id, user_id) VALUES ($1, $2) RETURNING id$$,
    'prepare get_or_create_chat_thread (insert)');
select lives_ok($$prepare insert_msg as
    INSERT INTO public.chat_messages (thread_id, role, content) VALUES ($1, $2, $3) RETURNING id$$,
    'prepare repository.insert_chat_message');
select lives_ok($$prepare create_job as
    INSERT INTO public.generation_jobs (id, map_id, triggered_by_message_id, job_type, target_node_id, callback_token_hash)
    VALUES ($1, $2, $3, $4, $5, $6)
    RETURNING *$$, 'prepare repository.create_generation_job');
select lives_ok($$prepare has_active as
    SELECT 1 FROM public.generation_jobs
     WHERE map_id = $1 AND job_type IN ('generate', 'regenerate_full')
       AND status IN ('pending', 'running') LIMIT 1$$, 'prepare repository.has_active_job');
select lives_ok($$prepare has_nodes as
    SELECT 1 FROM public.nodes WHERE map_id = $1 LIMIT 1$$, 'prepare maps.routes.generate (content check)');
select lives_ok($$prepare reject_compensation as
    UPDATE public.generation_jobs
       SET status = 'rejected',
           completed_at = now(),
           progress_percent = 0,
           error_message = $2
     WHERE id = $1 AND started_at IS NULL$$, 'prepare maps.routes enqueue-failure compensation');
select lives_ok($$prepare callback_auth as
    SELECT id, map_id, job_type, status, callback_token_hash FROM public.generation_jobs WHERE id = $1$$,
    'prepare internal.verify_callback_auth');
select lives_ok($$prepare progress as
    UPDATE public.generation_jobs
       SET status = 'running',
           progress_percent = GREATEST(progress_percent, $2),
           started_at = COALESCE(started_at, now())
     WHERE id = $1 AND status IN ('pending', 'running')$$, 'prepare internal.report_progress');
select lives_ok($$prepare lock_job as
    SELECT id, map_id, job_type, status, started_at FROM public.generation_jobs WHERE id = $1 FOR UPDATE$$,
    'prepare internal.report_complete (lock)');
select lives_ok($$prepare insert_seed as
    INSERT INTO public.seed_assets
        (generation_job_id, asset_type, file_name, storage_key, mime_type,
         content_text, file_size_bytes, checksum_sha256)
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
    RETURNING *$$, 'prepare repository.insert_seed_asset');
select lives_ok($$prepare insert_node as
    INSERT INTO public.nodes
        (id, map_id, label, description, pos_x, pos_y,
         background_color, text_color, node_type, source_job_id, node_data)
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)$$, 'prepare repository.persist_generated_graph (nodes)');
select lives_ok($$prepare insert_edge as
    INSERT INTO public.edges (id, map_id, source_node_id, target_node_id, label, edge_data)
    VALUES ($1, $2, $3, $4, $5, $6)$$, 'prepare repository.persist_generated_graph (edges)');
select lives_ok($$prepare complete_job as
    UPDATE public.generation_jobs
       SET status = 'completed',
           progress_percent = 100,
           started_at = COALESCE(started_at, now()),
           completed_at = now(),
           error_message = NULL
     WHERE id = $1$$, 'prepare internal.report_complete (status)');
select lives_ok($$prepare map_owner as
    SELECT user_id FROM public.mind_maps WHERE id = $1$$, 'prepare internal.report_complete (owner)');
select lives_ok($$prepare fail_job as
    UPDATE public.generation_jobs
       SET status = $2,
           started_at = CASE WHEN $2 = 'rejected' THEN NULL ELSE COALESCE(started_at, now()) END,
           completed_at = now(),
           progress_percent = CASE WHEN $2 = 'rejected' THEN 0 ELSE LEAST(progress_percent, 99) END,
           error_message = $3
     WHERE id = $1$$, 'prepare internal.report_fail (reuses $2 as status and in CASE)');
select lives_ok($$prepare has_seed as
    SELECT 1 FROM public.seed_assets a JOIN public.generation_jobs j ON j.id = a.generation_job_id
     WHERE j.map_id = $1 AND a.content_text IS NOT NULL LIMIT 1$$, 'prepare maps.routes.regenerate (seed check)');
select lives_ok($$prepare delete_edges as DELETE FROM public.edges WHERE map_id = $1$$,
    'prepare repository.replace_map_graph (edges)');
select lives_ok($$prepare delete_nodes as DELETE FROM public.nodes WHERE map_id = $1$$,
    'prepare repository.replace_map_graph (nodes)');
select lives_ok($$prepare latest_job as
    SELECT id, map_id, job_type, status, progress_percent, error_message, created_at, started_at, completed_at
      FROM public.generation_jobs WHERE map_id = $1 ORDER BY created_at DESC LIMIT 1$$,
    'prepare maps.routes.latest_job');
select lives_ok($$prepare get_job as
    SELECT id, map_id, job_type, status, progress_percent, error_message, created_at, started_at, completed_at
      FROM public.generation_jobs WHERE id = $1 AND map_id = $2$$, 'prepare maps.routes.get_job');
select lives_ok($$prepare chat_history as
    SELECT m.id, m.role, m.content, m.created_at
      FROM public.chat_messages m JOIN public.chat_threads t ON t.id = m.thread_id
     WHERE t.map_id = $1 ORDER BY m.created_at, m.id$$, 'prepare repository.get_chat_messages');
select lives_ok($$prepare graph_nodes as
    SELECT id, label, description, pos_x, pos_y, background_color, text_color,
           width, height, node_type, node_data
      FROM public.nodes WHERE map_id = $1$$, 'prepare maps.routes.get_graph (nodes)');
select lives_ok($$prepare graph_edges as
    SELECT id, source_node_id, target_node_id, label, edge_data FROM public.edges WHERE map_id = $1$$,
    'prepare maps.routes.get_graph (edges)');
select lives_ok($$prepare insert_embedding as
    INSERT INTO public.embeddings
        (generation_job_id, map_id, chunk_text, pinecone_vector_id, embedding_model, source, source_title, source_url)
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
    RETURNING id$$, 'prepare repository.insert_embedding');
select lives_ok($$prepare insert_embeddings as
    INSERT INTO public.embeddings
        (generation_job_id, map_id, chunk_text, pinecone_vector_id, embedding_model,
         source, source_title, source_url, retrieved_at)
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)$$, 'prepare repository.insert_embeddings');
select lives_ok($$prepare record_failure as
    UPDATE public.generation_jobs
       SET status = 'failed',
           started_at = COALESCE(started_at, now()),
           completed_at = now(),
           progress_percent = LEAST(progress_percent, 99),
           error_message = $2
     WHERE map_id = $1
       AND job_type IN ('generate', 'regenerate_full')
       AND status IN ('pending', 'running')$$, 'prepare services.runner.record_failure');
select lives_ok($$prepare delete_map as DELETE FROM public.mind_maps WHERE id = $1$$,
    'prepare maps.routes.delete_map');

-- =====================================================================
-- Flow 1: POST /api/maps, then POST /api/maps/{id}/generate
-- =====================================================================
execute create_map('a0000000-0000-0000-0000-000000000001', 'Photosynthesis');
create temp table t_ids as
    select (select id from public.mind_maps where title = 'Photosynthesis') as map_id;
select map_id from t_ids \gset

select is(
    (select count(*)::int from public.mind_maps m join t_ids t on t.map_id = m.id),
    1, 'map created for the authenticated user');

-- get_or_create_chat_thread + user turn + job, in one transaction (as in the route)
do $$
declare m uuid := (select map_id from t_ids);
        th uuid; msg uuid;
begin
    insert into public.chat_threads (map_id, user_id)
    values (m, 'a0000000-0000-0000-0000-000000000001') returning id into th;
    insert into public.chat_messages (thread_id, role, content)
    values (th, 'user', 'Generate mind map for topic: ''Photosynthesis''') returning id into msg;
    perform set_config('test.thread_id', th::text, true);
    perform set_config('test.msg_id', msg::text, true);
end $$;

select lives_ok(format($$execute create_job('f0000000-0000-0000-0000-000000000001', %L, %L, 'generate', NULL, %L)$$,
    (select map_id from t_ids), current_setting('test.msg_id'), encode(sha256('cb-token'), 'hex')),
    'generate job is created with id, trigger message and callback token hash');
select throws_ok(format($$execute create_job('f0000000-0000-0000-0000-000000000002', %L, NULL, 'regenerate_full', NULL, NULL)$$,
    (select map_id from t_ids)),
    '23505', 'duplicate key value violates unique constraint "uq_generation_jobs_active_full_map"',
    'second active job raises the exact constraint name ActiveJobAlreadyExists looks for');

select is((select callback_token_hash::text from public.generation_jobs where id = 'f0000000-0000-0000-0000-000000000001'),
    encode(sha256('cb-token'), 'hex'), 'callback auth can read back the stored token hash');

-- Worker: progress (pending -> running), clamped to 99 by the route
execute progress('f0000000-0000-0000-0000-000000000001', 42);
select results_eq(
    $$select status::text, progress_percent from public.generation_jobs where id = 'f0000000-0000-0000-0000-000000000001'$$,
    $$values ('running', 42)$$, 'progress callback moves the job to running');

-- Worker: complete. Seed assets exactly as llm/mindmap_llm/seed/normalizer.py builds them.
select lives_ok(format($$execute insert_seed('f0000000-0000-0000-0000-000000000001', 'pdf', 'notes.pdf', %L,
        'application/pdf', 'extracted pdf text', 1234, %L)$$,
    'a0000000-0000-0000-0000-000000000001/' || (select map_id from t_ids) || '/f0000000-0000-0000-0000-000000000001/0_notes.pdf',
    encode(sha256('pdf-bytes'), 'hex')),
    'PDF seed asset from the worker is stored');
select lives_ok($$execute insert_seed('f0000000-0000-0000-0000-000000000001', 'text', NULL, NULL, NULL,
        'Photosynthesis\n\nlight, chlorophyll', 33, NULL)$$,
    'text seed asset from the worker is stored');

select lives_ok(format($$execute insert_node('10000000-0000-0000-0000-000000000001', %L, 'Photosynthesis', 'root', 0, 0,
        '#1E88E5', '#FFFFFF', 'default', 'f0000000-0000-0000-0000-000000000001', NULL)$$, (select map_id from t_ids)),
    'root node is stored with source_job_id');
select lives_ok(format($$execute insert_node('10000000-0000-0000-0000-000000000002', %L, 'Light reactions', NULL, 250, 0,
        NULL, NULL, 'default', 'f0000000-0000-0000-0000-000000000001', '{"original_id":"n2"}')$$, (select map_id from t_ids)),
    'child node with JSON node_data is stored');
select lives_ok(format($$execute insert_edge('20000000-0000-0000-0000-000000000001', %L,
        '10000000-0000-0000-0000-000000000001', '10000000-0000-0000-0000-000000000002', 'includes', NULL)$$,
        (select map_id from t_ids)),
    'edge is stored');
select throws_ok(format($$execute insert_edge('20000000-0000-0000-0000-000000000002', %L,
        '10000000-0000-0000-0000-000000000001', '10000000-0000-0000-0000-000000000002', 'includes', NULL)$$,
        (select map_id from t_ids)),
    '23505', null,
    'a duplicate edge (same source, target, label) in a worker result is rejected');

execute complete_job('f0000000-0000-0000-0000-000000000001');
select results_eq(
    $$select status::text, progress_percent, started_at is not null, completed_at is not null
        from public.generation_jobs where id = 'f0000000-0000-0000-0000-000000000001'$$,
    $$values ('completed', 100, true, true)$$, 'complete callback satisfies the job lifecycle CHECK');

execute insert_msg(current_setting('test.thread_id')::uuid, 'assistant', 'Generated mind map with 2 nodes and 1 edges.');
select is((select count(*)::int from public.chat_messages where thread_id = current_setting('test.thread_id')::uuid),
    2, 'user and assistant turns are in the thread');

-- =====================================================================
-- Flow 2: POST /api/maps/{id}/regenerate (full)
-- =====================================================================
select ok(exists(select 1 from public.seed_assets a join public.generation_jobs j on j.id = a.generation_job_id
                 where j.map_id = (select map_id from t_ids) and a.content_text is not null),
    'regenerate finds the original seed');
select lives_ok(format($$execute create_job('f0000000-0000-0000-0000-000000000003', %L, NULL, 'regenerate_full', NULL, %L)$$,
    (select map_id from t_ids), encode(sha256('cb-token-2'), 'hex')),
    'regenerate_full job can start once the first job finished');

execute delete_edges(:'map_id');
execute delete_nodes(:'map_id');
select lives_ok(format($$execute insert_node('10000000-0000-0000-0000-000000000003', %L, 'Photosynthesis v2', NULL, 0, 0,
        NULL, NULL, 'default', 'f0000000-0000-0000-0000-000000000003', NULL)$$, (select map_id from t_ids)),
    'replace_map_graph re-inserts nodes after deleting the old graph');
execute complete_job('f0000000-0000-0000-0000-000000000003');
select is((select count(*)::int from public.nodes where map_id = (select map_id from t_ids)), 1,
    'full regeneration replaced the graph');

-- =====================================================================
-- Flow 3: failures
-- =====================================================================
-- Worker rejects a job that never started -> 'rejected'
select lives_ok(format($$execute create_job('f0000000-0000-0000-0000-000000000004', %L, NULL, 'regenerate_full', NULL, NULL)$$,
    (select map_id from t_ids)), 'job 4 created');
select lives_ok($$execute fail_job('f0000000-0000-0000-0000-000000000004', 'rejected', 'No usable seed content')$$,
    'fail callback can mark a never-started job rejected');

-- Worker fails a running job -> 'failed', progress kept below 100
select lives_ok(format($$execute create_job('f0000000-0000-0000-0000-000000000005', %L, NULL, 'regenerate_full', NULL, NULL)$$,
    (select map_id from t_ids)), 'job 5 created');
execute progress('f0000000-0000-0000-0000-000000000005', 60);
select lives_ok($$execute fail_job('f0000000-0000-0000-0000-000000000005', 'failed', 'LLM timeout')$$,
    'fail callback can mark a running job failed');

-- Queue/upload failure compensation in the routes -> 'rejected'
select lives_ok(format($$execute create_job('f0000000-0000-0000-0000-000000000006', %L, NULL, 'generate', NULL, NULL)$$,
    (select map_id from t_ids)), 'job 6 created');
select lives_ok($$execute reject_compensation('f0000000-0000-0000-0000-000000000006', 'Failed to queue generation job.')$$,
    'enqueue-failure compensation marks the job rejected');

-- Legacy runner.record_failure on a stuck pending job
select lives_ok(format($$execute create_job('f0000000-0000-0000-0000-000000000007', %L, NULL, 'generate', NULL, NULL)$$,
    (select map_id from t_ids)), 'job 7 created');
select lives_ok(format($$execute record_failure(%L, 'Generation failed unexpectedly. Please try again.')$$,
    (select map_id from t_ids)), 'record_failure marks a stuck job failed');

select results_eq(
    format($$select id::text, status::text from public.generation_jobs where map_id = %L
             and id in ('f0000000-0000-0000-0000-000000000004','f0000000-0000-0000-0000-000000000005',
                        'f0000000-0000-0000-0000-000000000006','f0000000-0000-0000-0000-000000000007')
             order by id$$, (select map_id from t_ids)),
    $$values ('f0000000-0000-0000-0000-000000000004','rejected'),
             ('f0000000-0000-0000-0000-000000000005','failed'),
             ('f0000000-0000-0000-0000-000000000006','rejected'),
             ('f0000000-0000-0000-0000-000000000007','failed')$$,
    'every failure path ends in a lifecycle-valid terminal status');

-- =====================================================================
-- Flow 4: DELETE /api/maps/{id} cascades
-- =====================================================================
execute delete_map(:'map_id');
select is(
    (select count(*)::int from public.generation_jobs where map_id = (select map_id from t_ids))
  + (select count(*)::int from public.nodes where map_id = (select map_id from t_ids))
  + (select count(*)::int from public.chat_threads where map_id = (select map_id from t_ids))
  + (select count(*)::int from public.seed_assets),
    0, 'deleting a map removes its jobs, nodes, chat and seed assets');

select * from finish();
rollback;
