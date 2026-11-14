-- S2-DB2 / Issue #76: snapshots versioning and RLS.
BEGIN;
CREATE EXTENSION IF NOT EXISTS pgtap WITH SCHEMA extensions;
SELECT plan(19);

SELECT has_column('public', 'nodes', 'version', 'nodes.version exists');
SELECT col_type_is('public', 'nodes', 'version', 'integer', 'nodes.version is integer');
SELECT has_column('public', 'map_snapshots', 'version_no', 'snapshot version_no exists');
SELECT has_column('public', 'map_snapshots', 'job_id', 'snapshot job_id exists');
SELECT has_column('public', 'map_snapshots', 'graph', 'snapshot graph exists');
SELECT col_type_is('public', 'map_snapshots', 'graph', 'jsonb', 'graph is jsonb');
SELECT has_function('public', 'record_map_snapshot', ARRAY['uuid','uuid','uuid','text'], 'snapshot helper exists');

INSERT INTO auth.users(id,email) VALUES
  ('a0000000-0000-0000-0000-000000000091','snap-owner@example.com'),
  ('a0000000-0000-0000-0000-000000000092','snap-other@example.com');
INSERT INTO public.mind_maps(id,user_id,title) VALUES
  ('b0000000-0000-0000-0000-000000000091','a0000000-0000-0000-0000-000000000091','Snapshot test');
INSERT INTO public.nodes(id,map_id,label) VALUES
  ('c0000000-0000-0000-0000-000000000091','b0000000-0000-0000-0000-000000000091','Original');

SELECT is((SELECT version FROM public.nodes WHERE id='c0000000-0000-0000-0000-000000000091'),1,
  'node version starts at 1');
UPDATE public.nodes SET label='Updated' WHERE id='c0000000-0000-0000-0000-000000000091';
SELECT is((SELECT version FROM public.nodes WHERE id='c0000000-0000-0000-0000-000000000091'),2,
  'editing node bumps version');

SELECT lives_ok($$SELECT public.record_map_snapshot('b0000000-0000-0000-0000-000000000091')$$,
  'snapshot helper captures graph');
SELECT is((SELECT version_no FROM public.map_snapshots WHERE map_id='b0000000-0000-0000-0000-000000000091'),1,
  'first snapshot has version number 1');
SELECT is((SELECT graph->'nodes'->0->>'label' FROM public.map_snapshots WHERE map_id='b0000000-0000-0000-0000-000000000091'),
  'Updated', 'snapshot graph contains node data');
SELECT lives_ok($$INSERT INTO public.map_snapshots(map_id,nodes_snapshot,edges_snapshot)
  VALUES ('b0000000-0000-0000-0000-000000000091','[]'::jsonb,'[]'::jsonb)$$,
  'legacy backend insert remains valid');
SELECT is((SELECT MAX(version_no) FROM public.map_snapshots WHERE map_id='b0000000-0000-0000-0000-000000000091'),2,
  'second snapshot receives next version');
SELECT throws_ok($$UPDATE public.map_snapshots SET graph='{}'::jsonb
  WHERE map_id='b0000000-0000-0000-0000-000000000091'$$,
  '55000', NULL, 'snapshot content cannot be edited');

SELECT set_config('request.jwt.claims',
  '{"sub":"a0000000-0000-0000-0000-000000000092","role":"authenticated"}',true);
SET LOCAL ROLE authenticated;
SELECT is((SELECT count(*)::integer FROM public.map_snapshots WHERE map_id='b0000000-0000-0000-0000-000000000091'),
  0,'non-owner cannot read snapshots');
RESET ROLE;
SELECT set_config('request.jwt.claims',
  '{"sub":"a0000000-0000-0000-0000-000000000091","role":"authenticated"}',true);
SET LOCAL ROLE authenticated;
SELECT is((SELECT count(*)::integer FROM public.map_snapshots WHERE map_id='b0000000-0000-0000-0000-000000000091'),
  2,'owner can read snapshots');
RESET ROLE;

SELECT lives_ok($$DELETE FROM public.mind_maps WHERE id='b0000000-0000-0000-0000-000000000091'$$,
  'deleting map succeeds with snapshots present');
SELECT is((SELECT count(*)::integer FROM public.map_snapshots WHERE map_id='b0000000-0000-0000-0000-000000000091'),
  0,'deleting map cascades to snapshots');

SELECT * FROM finish();
ROLLBACK;
