-- S2-DB4 / Issue #78: pgTAP security and search coverage.
BEGIN;
CREATE EXTENSION IF NOT EXISTS pgtap WITH SCHEMA extensions;
SELECT plan(14);

SELECT has_extension('pg_trgm', 'pg_trgm is enabled');
SELECT has_index('public','mind_maps','idx_mind_maps_search_fts', 'map full text GIN index exists');
SELECT has_index('public','nodes','idx_nodes_label_fts', 'node label full text GIN index exists');
SELECT has_index('public','nodes','idx_nodes_label_trgm', 'node label trigram index exists');
SELECT has_function('public','search_maps',ARRAY['uuid','text','integer'], 'search RPC exists');

INSERT INTO auth.users (id,email) VALUES
  ('f7800000-0000-0000-0000-000000000001','issue78alice@example.invalid'),
  ('f7800000-0000-0000-0000-000000000002','issue78bob@example.invalid');
INSERT INTO public.mind_maps(id,user_id,title,description,is_public) VALUES
  ('f7810000-0000-0000-0000-000000000001','f7800000-0000-0000-0000-000000000001','Photosynthesis','Chlorophyll absorbs sunlight',false),
  ('f7810000-0000-0000-0000-000000000002','f7800000-0000-0000-0000-000000000002','Photosynthesis private','Sunlight secret',false),
  ('f7810000-0000-0000-0000-000000000003','f7800000-0000-0000-0000-000000000002','Public sunlight','Photosynthesis is public',true),
  ('f7810000-0000-0000-0000-000000000004','f7800000-0000-0000-0000-000000000001','Physics notes','Waves',false);
INSERT INTO public.nodes (map_id,label) VALUES
  ('f7810000-0000-0000-0000-000000000004','Quantum mechanics');

SELECT set_config('request.jwt.claims',
  '{"sub":"f7800000-0000-0000-0000-000000000001","role":"authenticated"}',true);
SET LOCAL ROLE authenticated;

SELECT is((SELECT count(*)::int FROM public.search_maps('f7800000-0000-0000-0000-000000000001','photosynthesis',20)),1,'Alice only finds her map');
SELECT is((SELECT count(*)::int FROM public.search_maps('f7800000-0000-0000-0000-000000000002','photosynthesis',20)),0,'Alice cannot impersonate Bob by supplying his ID');
SELECT is((SELECT count(*)::int FROM public.search_maps('f7800000-0000-0000-0000-000000000001','quantum',20)),1,'node label is searchable');
SELECT ok((SELECT snippet IS NOT NULL AND length(snippet)>0 FROM public.search_maps('f7800000-0000-0000-0000-000000000001','chlorophyll',20) LIMIT 1),'search returns snippet');
SELECT is((SELECT count(*)::int FROM public.search_maps('f7800000-0000-0000-0000-000000000001','',20)),0,'empty term returns no maps');
SELECT is((SELECT count(*)::int FROM public.search_maps('f7800000-0000-0000-0000-000000000001','photosynthesis',1)),1,'limit is respected');

RESET ROLE;
SELECT set_config('request.jwt.claims',
  '{"sub":"f7800000-0000-0000-0000-000000000002","role":"authenticated"}',true);
SET LOCAL ROLE authenticated;
SELECT is((SELECT count(*)::int FROM public.search_maps('f7800000-0000-0000-0000-000000000002','photosynthesis',20)),2,'Bob finds his own private and public maps');
SELECT is((SELECT count(*)::int FROM public.search_maps('f7800000-0000-0000-0000-000000000001','quantum',20)),0,'Bob cannot impersonate Alice');

RESET ROLE;
SELECT set_config('request.jwt.claims','{"role":"anon"}',true);
SET LOCAL ROLE anon;
SELECT throws_ok($$SELECT * FROM public.search_maps('f7800000-0000-0000-0000-000000000002','photosynthesis',20)$$,
  '42501',NULL,'anonymous users cannot execute search RPC');

RESET ROLE;
SELECT * FROM finish();
ROLLBACK;
