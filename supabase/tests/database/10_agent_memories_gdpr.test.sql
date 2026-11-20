-- S3-DB2 / Issue #163: schema, per-user isolation, admin denial, GDPR, cascades.
BEGIN;
CREATE EXTENSION IF NOT EXISTS pgtap WITH SCHEMA extensions;
SELECT plan(34);

SELECT has_column('public', 'agent_memories', 'kind', 'S3 kind exists');
SELECT col_type_is('public', 'agent_memories', 'kind', 'text', 'kind is text');
SELECT has_column('public', 'agent_memories', 'importance', 'importance exists');
SELECT col_type_is('public', 'agent_memories', 'importance', 'double precision', 'importance is float');
SELECT col_type_is('public', 'agent_memories', 'last_accessed_at', 'timestamp with time zone', 'last access is timestamptz');
SELECT col_type_is('public', 'agent_memories', 'disabled_at', 'timestamp with time zone', 'disable marker is timestamptz');
SELECT ok(to_regclass('public.idx_agent_memories_user_map') IS NOT NULL, 'user/map index exists');
SELECT ok(to_regclass('public.idx_agent_memories_user_kind_disabled') IS NOT NULL, 'user/kind/disabled index exists');
SELECT has_view('public', 'agent_memories_gdpr_export', 'GDPR export view exists');
SELECT ok(EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'public.agent_memories'::regclass
 AND conname = 'agent_memories_user_auth_fkey'
 AND confrelid = 'auth.users'::regclass AND confdeltype = 'c'),
 'user_id has cascade foreign key to auth.users');
SELECT ok((SELECT reloptions @> ARRAY['security_invoker=true']
           FROM pg_class WHERE oid='public.agent_memories_gdpr_export'::regclass),
          'GDPR export view enforces invoker RLS');
SELECT ok(has_table_privilege('authenticated', 'public.agent_memories', 'DELETE'),
          'authenticated users have DELETE table privilege');
SELECT ok(has_column_privilege('authenticated', 'public.agent_memories', 'content', 'UPDATE'),
          'authenticated users can update memory content');
SELECT ok(NOT has_column_privilege('authenticated', 'public.agent_memories', 'user_id', 'UPDATE'),
          'authenticated users cannot transfer memory ownership');
SELECT ok(NOT has_table_privilege('authenticated', 'public.agent_memories', 'INSERT'),
          'clients still cannot insert memory directly');

INSERT INTO auth.users(id, email) VALUES
 ('a1630000-0000-0000-0000-000000000001', 'alice163@example.invalid'),
 ('a1630000-0000-0000-0000-000000000002', 'bob163@example.invalid'),
 ('a1630000-0000-0000-0000-000000000003', 'admin163@example.invalid');
UPDATE public.profiles SET role = 'admin' WHERE id = 'a1630000-0000-0000-0000-000000000003';
INSERT INTO public.mind_maps(id, user_id, title) VALUES
 ('b1630000-0000-0000-0000-000000000001', 'a1630000-0000-0000-0000-000000000001', 'Alice map'),
 ('b1630000-0000-0000-0000-000000000002', 'a1630000-0000-0000-0000-000000000002', 'Bob map');
INSERT INTO public.agent_memories(id, user_id, map_id, memory_type, kind, content) VALUES
 ('c1630000-0000-0000-0000-000000000001', 'a1630000-0000-0000-0000-000000000001', NULL, 'preference', 'preference', 'Alice global'),
 ('c1630000-0000-0000-0000-000000000002', 'a1630000-0000-0000-0000-000000000001', 'b1630000-0000-0000-0000-000000000001', 'context', 'fact', 'Alice mapped'),
 ('c1630000-0000-0000-0000-000000000003', 'a1630000-0000-0000-0000-000000000001', NULL, 'decision', 'decision', 'Alice removable'),
 ('c1630000-0000-0000-0000-000000000004', 'a1630000-0000-0000-0000-000000000002', NULL, 'fact', 'fact', 'Bob global'),
 ('c1630000-0000-0000-0000-000000000005', 'a1630000-0000-0000-0000-000000000002', 'b1630000-0000-0000-0000-000000000002', 'fact', 'fact', 'Bob mapped'),
 ('c1630000-0000-0000-0000-000000000006', 'a1630000-0000-0000-0000-000000000003', NULL, 'fact', 'fact', 'Admin secret');

SELECT is((SELECT importance FROM public.agent_memories WHERE id = 'c1630000-0000-0000-0000-000000000001'),
          1.0::double precision, 'importance defaults to one');
SELECT ok((SELECT last_accessed_at IS NOT NULL FROM public.agent_memories WHERE id = 'c1630000-0000-0000-0000-000000000001'),
          'last access defaults to now');
SELECT throws_ok($$INSERT INTO public.agent_memories(user_id, kind, content)
 VALUES ('a1630000-0000-0000-0000-000000000001', 'other', 'invalid')$$,
 '23514', NULL, 'unsupported kind rejected');

SELECT set_config('request.jwt.claims',
 '{"sub":"a1630000-0000-0000-0000-000000000001","role":"authenticated"}', true);
SET LOCAL ROLE authenticated;
SELECT is((SELECT count(*)::int FROM public.agent_memories), 3, 'Alice sees only her own three memories');
SELECT is((SELECT jsonb_array_length(memories) FROM public.agent_memories_gdpr_export), 3,
          'Alice GDPR bundle includes all of her memories');
SELECT is((SELECT count(*)::int FROM public.agent_memories_gdpr_export WHERE user_id = 'a1630000-0000-0000-0000-000000000002'), 0,
          'Alice cannot export Bob memory');
SELECT lives_ok($$UPDATE public.agent_memories SET content = 'Alice changed', disabled_at = now()
 WHERE id = 'c1630000-0000-0000-0000-000000000001'$$, 'Alice may update her own memory');
SELECT is((SELECT count(*)::int FROM public.agent_memories
 WHERE content = 'Alice changed' AND disabled_at IS NOT NULL), 1,
 'Alice update and disable are visible');
SELECT is((SELECT jsonb_array_length(memories) FROM public.agent_memories_gdpr_export), 3,
 'GDPR export includes disabled memories');
SELECT lives_ok($$DELETE FROM public.agent_memories WHERE id = 'c1630000-0000-0000-0000-000000000003'$$,
 'Alice may delete her own memory');
SELECT is((SELECT count(*)::int FROM public.agent_memories), 2, 'Alice sees two memories after deletion');
SELECT lives_ok($$UPDATE public.agent_memories SET content = 'malicious'
 WHERE id = 'c1630000-0000-0000-0000-000000000004'$$,
 'cross-user update cannot change any row');
SELECT lives_ok($$DELETE FROM public.agent_memories
 WHERE id = 'c1630000-0000-0000-0000-000000000004'$$,
 'cross-user deletion cannot remove any row');
RESET ROLE;
SELECT is((SELECT content FROM public.agent_memories WHERE id = 'c1630000-0000-0000-0000-000000000004'),
 'Bob global', 'Bob memory unchanged by Alice update');

SELECT set_config('request.jwt.claims',
 '{"sub":"a1630000-0000-0000-0000-000000000003","role":"authenticated"}', true);
SET LOCAL ROLE authenticated;
SELECT is((SELECT count(*)::int FROM public.agent_memories), 0, 'admin cannot SELECT memory');
SELECT is((SELECT count(*)::int FROM public.agent_memories_gdpr_export), 0, 'admin cannot export memory');
RESET ROLE;
SELECT set_config('request.jwt.claims', '{"role":"anon"}', true);
SET LOCAL ROLE anon;
SELECT throws_ok($$SELECT * FROM public.agent_memories_gdpr_export$$,
 '42501', NULL, 'anonymous users cannot export private memory');
RESET ROLE;

DELETE FROM public.mind_maps WHERE id = 'b1630000-0000-0000-0000-000000000002';
SELECT is((SELECT count(*)::int FROM public.agent_memories WHERE user_id = 'a1630000-0000-0000-0000-000000000002'),
 1, 'deleting Bob map cascades mapped memory but preserves global memory');
DELETE FROM auth.users WHERE id = 'a1630000-0000-0000-0000-000000000001';
SELECT is((SELECT count(*)::int FROM public.agent_memories WHERE user_id = 'a1630000-0000-0000-0000-000000000001'),
 0, 'deleting Alice account cascades all remaining memories');

SELECT * FROM finish();
ROLLBACK;
