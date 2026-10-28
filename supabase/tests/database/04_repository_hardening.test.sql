-- SQL added to backend/db/repository.py so the backend survives real worker
-- output and crashed workers, replayed with untyped parameters like asyncpg:
--   * get_or_create_chat_thread()  INSERT ... ON CONFLICT (map_id) DO NOTHING
--   * persist_generated_graph()    edges INSERT ... ON CONFLICT DO NOTHING
--   * has_active_job()             expires abandoned jobs in the same statement
--   * expire_stale_jobs()
--   * update_job_status()          every target status satisfies the lifecycle CHECK
--   * delete_mind_map()            returns the Storage keys to remove
-- Also the statements in backend/app/maps/routes.py that 03 does not cover
-- (quick-generate polling, node-regeneration intake check).
-- Run with: supabase test db
begin;
create extension if not exists pgtap with schema extensions;
select plan(26);

insert into auth.users (id, email, raw_user_meta_data)
values ('b0000000-0000-0000-0000-000000000001', 'bob@example.com', '{"full_name":"Bob"}');
insert into public.mind_maps (id, user_id, title)
values ('b1000000-0000-0000-0000-000000000001', 'b0000000-0000-0000-0000-000000000001', 'Hardening');

-- ---------------------------------------------------------------------
-- Prepared statements (copied from backend/db/repository.py)
-- ---------------------------------------------------------------------
select lives_ok($$prepare thread_upsert as
    INSERT INTO public.chat_threads (map_id, user_id) VALUES ($1, $2) ON CONFLICT (map_id) DO NOTHING RETURNING id$$,
    'prepare get_or_create_chat_thread (insert, conflict-safe)');
select lives_ok($$prepare create_job as
    INSERT INTO public.generation_jobs
        (id, map_id, triggered_by_message_id, job_type, target_node_id,
         callback_token_hash)
    VALUES ($1, $2, $3, $4, $5, $6)
    RETURNING *$$, 'prepare create_generation_job');
select lives_ok($$prepare has_active as
    WITH expired AS (
        UPDATE public.generation_jobs
           SET status = CASE WHEN started_at IS NULL THEN 'rejected' ELSE 'failed' END,
               completed_at = now(),
               progress_percent = CASE WHEN started_at IS NULL THEN 0
                                       ELSE LEAST(progress_percent, 99) END,
               error_message = $3
         WHERE map_id = $1
           AND job_type IN ('generate', 'regenerate_full')
           AND status IN ('pending', 'running')
           AND COALESCE(started_at, created_at) < now() - make_interval(secs => $2)
        RETURNING id
    )
    SELECT 1 FROM public.generation_jobs
     WHERE map_id = $1
       AND job_type IN ('generate', 'regenerate_full')
       AND status IN ('pending', 'running')
       AND id NOT IN (SELECT id FROM expired)
     LIMIT 1$$, 'prepare has_active_job (with stale-job sweep)');
select lives_ok($$prepare insert_node as
    INSERT INTO public.nodes
        (id, map_id, label, description, pos_x, pos_y,
         background_color, text_color, node_type, source_job_id, node_data)
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)$$, 'prepare persist_generated_graph (nodes)');
select lives_ok($$prepare insert_edge as
    INSERT INTO public.edges (id, map_id, source_node_id, target_node_id, label, edge_data)
    VALUES ($1, $2, $3, $4, $5, $6)
    ON CONFLICT DO NOTHING$$, 'prepare persist_generated_graph (edges, duplicate-safe)');
select lives_ok($$prepare status_running as
    UPDATE public.generation_jobs SET status = $2, started_at = COALESCE(started_at, now()),
        completed_at = NULL, error_message = NULL, progress_percent = $3 WHERE id = $1$$,
    'prepare update_job_status(RUNNING)');
select lives_ok($$prepare status_completed as
    UPDATE public.generation_jobs SET status = $2, started_at = COALESCE(started_at, now()),
        completed_at = COALESCE(completed_at, now()), progress_percent = 100, error_message = NULL
     WHERE id = $1$$, 'prepare update_job_status(COMPLETED)');
select lives_ok($$prepare status_failed as
    UPDATE public.generation_jobs SET status = $2, started_at = COALESCE(started_at, now()),
        completed_at = COALESCE(completed_at, now()), progress_percent = LEAST(progress_percent, 99),
        error_message = $3 WHERE id = $1$$, 'prepare update_job_status(FAILED)');
select lives_ok($$prepare status_rejected as
    UPDATE public.generation_jobs SET status = $2, started_at = NULL,
        completed_at = COALESCE(completed_at, now()), progress_percent = 0, error_message = $3
     WHERE id = $1$$, 'prepare update_job_status(REJECTED)');
select lives_ok($$prepare map_storage_keys as
    SELECT a.storage_key
      FROM public.seed_assets a
      JOIN public.generation_jobs j ON j.id = a.generation_job_id
     WHERE j.map_id = $1 AND a.storage_key IS NOT NULL$$, 'prepare delete_mind_map (storage keys)');
select lives_ok($$prepare poll_job as
    SELECT status, error_message FROM public.generation_jobs WHERE id = $1$$,
    'prepare maps.routes.quick_generate (poll)');
select lives_ok($$prepare quick_nodes as
    SELECT * FROM public.nodes WHERE map_id = $1 ORDER BY created_at ASC$$,
    'prepare maps.routes.quick_generate (nodes)');
select lives_ok($$prepare node_on_map as
    SELECT 1 FROM public.nodes WHERE id = $1 AND map_id = $2$$,
    'prepare maps.routes.regenerate_node (intake check)');

-- ---------------------------------------------------------------------
-- Behaviour
-- ---------------------------------------------------------------------
execute thread_upsert('b1000000-0000-0000-0000-000000000001', 'b0000000-0000-0000-0000-000000000001');
select lives_ok($$execute thread_upsert('b1000000-0000-0000-0000-000000000001', 'b0000000-0000-0000-0000-000000000001')$$,
    'creating the thread twice does not fail (concurrent first requests)');
select is((select count(*)::int from public.chat_threads where map_id = 'b1000000-0000-0000-0000-000000000001'), 1,
    'still exactly one thread per map');

-- Worker result with a repeated edge: stored once, callback does not fail.
execute create_job('b2000000-0000-0000-0000-000000000001', 'b1000000-0000-0000-0000-000000000001', NULL, 'generate', NULL, 'h');
execute status_running('b2000000-0000-0000-0000-000000000001', 'running', 20);
execute insert_node('b3000000-0000-0000-0000-000000000001', 'b1000000-0000-0000-0000-000000000001', 'Root', NULL, 0, 0,
    NULL, NULL, 'root', 'b2000000-0000-0000-0000-000000000001', NULL);
execute insert_node('b3000000-0000-0000-0000-000000000002', 'b1000000-0000-0000-0000-000000000001', 'Child', NULL, 1, 1,
    NULL, NULL, 'default', 'b2000000-0000-0000-0000-000000000001', '{"original_id":"n2"}');
execute insert_edge('b4000000-0000-0000-0000-000000000001', 'b1000000-0000-0000-0000-000000000001',
    'b3000000-0000-0000-0000-000000000001', 'b3000000-0000-0000-0000-000000000002', NULL, NULL);
select lives_ok($$execute insert_edge('b4000000-0000-0000-0000-000000000002', 'b1000000-0000-0000-0000-000000000001',
    'b3000000-0000-0000-0000-000000000001', 'b3000000-0000-0000-0000-000000000002', NULL, NULL)$$,
    'a duplicate edge is skipped instead of failing the completion');
select is((select count(*)::int from public.edges where map_id = 'b1000000-0000-0000-0000-000000000001'), 1,
    'the duplicate edge was not stored');
select lives_ok($$execute status_completed('b2000000-0000-0000-0000-000000000001', 'completed')$$,
    'update_job_status(COMPLETED) is lifecycle-valid without an explicit progress value');
select ok(exists(select 1 from public.nodes where id = 'b3000000-0000-0000-0000-000000000002'
                 and map_id = 'b1000000-0000-0000-0000-000000000001'), 'regenerate_node intake finds the node');

-- Failure transitions with no progress/message supplied by the caller
execute create_job('b2000000-0000-0000-0000-000000000002', 'b1000000-0000-0000-0000-000000000001', NULL, 'generate', NULL, 'h');
select lives_ok($$execute status_failed('b2000000-0000-0000-0000-000000000002', 'failed', 'Generation failed')$$,
    'update_job_status(FAILED) works on a job that never reported progress');
execute create_job('b2000000-0000-0000-0000-000000000003', 'b1000000-0000-0000-0000-000000000001', NULL, 'generate', NULL, 'h');
select lives_ok($$execute status_rejected('b2000000-0000-0000-0000-000000000003', 'rejected', 'Queue unavailable')$$,
    'update_job_status(REJECTED) works on a pending job');

-- Abandoned jobs: a fresh active job blocks, a stale one is closed by the check itself.
execute create_job('b2000000-0000-0000-0000-000000000004', 'b1000000-0000-0000-0000-000000000001', NULL, 'regenerate_full', NULL, 'h');
execute status_running('b2000000-0000-0000-0000-000000000004', 'running', 50);
select results_eq(
    $$execute has_active('b1000000-0000-0000-0000-000000000001', 1800, 'Generation timed out. Please try again.')$$,
    $$values (1)$$, 'a fresh running job still blocks a new one');
-- age the job past the 1800 s threshold
update public.generation_jobs
   set created_at = created_at - interval '2 hours', started_at = started_at - interval '1 hour'
 where id = 'b2000000-0000-0000-0000-000000000004';
execute has_active('b1000000-0000-0000-0000-000000000001', 1800, 'Generation timed out. Please try again.');
select results_eq(
    $$select status, progress_percent, error_message from public.generation_jobs
       where id = 'b2000000-0000-0000-0000-000000000004'$$,
    $$values ('failed'::text, 50, 'Generation timed out. Please try again.'::text)$$,
    'has_active_job closes a stale running job as failed, keeping its progress');
select lives_ok($$execute create_job('b2000000-0000-0000-0000-000000000005', 'b1000000-0000-0000-0000-000000000001', NULL, 'generate', NULL, 'h')$$,
    'a new job can start after the stale one was closed');
update public.generation_jobs set created_at = created_at - interval '2 hours'
 where id = 'b2000000-0000-0000-0000-000000000005';
execute has_active('b1000000-0000-0000-0000-000000000001', 1800, 'Generation timed out. Please try again.');
select is((select status from public.generation_jobs where id = 'b2000000-0000-0000-0000-000000000005'),
    'rejected', 'a stale job that never started is closed as rejected');

-- delete_mind_map: collect Storage keys, then rows cascade
insert into public.seed_assets (generation_job_id, asset_type, file_name, storage_key, mime_type)
values ('b2000000-0000-0000-0000-000000000001', 'pdf', 'a.pdf',
        'b0000000-0000-0000-0000-000000000001/b1000000-0000-0000-0000-000000000001/b2000000-0000-0000-0000-000000000001/0_a.pdf',
        'application/pdf');
select results_eq($$execute map_storage_keys('b1000000-0000-0000-0000-000000000001')$$,
    $$values ('b0000000-0000-0000-0000-000000000001/b1000000-0000-0000-0000-000000000001/b2000000-0000-0000-0000-000000000001/0_a.pdf'::text)$$,
    'delete_mind_map finds the Storage keys to remove');

select * from finish();
rollback;
