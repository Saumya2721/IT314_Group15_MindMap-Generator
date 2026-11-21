-- S3-DB1 / Issue #162: admins get aggregate oversight, never raw user content.
BEGIN;
CREATE EXTENSION IF NOT EXISTS pgtap WITH SCHEMA extensions;
SELECT plan(38);

SELECT has_column('public', 'profiles', 'role', 'profiles.role exists');
SELECT col_type_is('public', 'profiles', 'role', 'text', 'profiles.role is text');
SELECT has_column('public', 'profiles', 'suspended_at', 'suspension timestamp exists');
SELECT col_type_is('public', 'profiles', 'suspended_at', 'timestamp with time zone', 'suspended_at is timestamptz');
SELECT has_table('public', 'admin_audit_log', 'admin audit table exists');
SELECT has_column('public', 'admin_audit_log', 'actor_id', 'actor recorded');
SELECT has_column('public', 'admin_audit_log', 'target_user_id', 'target is optional');
SELECT col_type_is('public', 'admin_audit_log', 'metadata', 'jsonb', 'audit metadata is jsonb');
SELECT is((SELECT relrowsecurity FROM pg_class WHERE oid = 'public.admin_audit_log'::regclass),
          true, 'audit log has RLS');
SELECT is((SELECT count(*)::int FROM pg_policies
           WHERE schemaname = 'public' AND permissive = 'RESTRICTIVE'
             AND policyname LIKE '%_admin_no_select'), 5,
          'five content tables have restrictive admin SELECT policies');
SELECT has_view('public', 'admin_generation_counts', 'generation count view exists');
SELECT has_view('public', 'admin_active_users', 'active user view exists');
SELECT has_view('public', 'admin_generation_failure_rates', 'failure rate view exists');
SELECT ok(NOT has_column_privilege('authenticated', 'public.profiles', 'role', 'INSERT'),
          'authenticated clients cannot insert profile role');
SELECT ok(NOT has_column_privilege('authenticated', 'public.profiles', 'role', 'UPDATE'),
          'authenticated clients cannot update profile role');
SELECT ok(NOT has_column_privilege('authenticated', 'public.profiles', 'suspended_at', 'INSERT'),
          'authenticated clients cannot set their own suspension');

-- Backend-equivalent setup (test owner): 2 users, an admin-owned public map,
-- node, edge, chat, and memory. Even the admin's own/public content must hide.
INSERT INTO auth.users (id, email) VALUES
    ('a1620000-0000-0000-0000-000000000001', 'admin162@example.invalid'),
    ('a1620000-0000-0000-0000-000000000002', 'regular162@example.invalid');
SELECT is((SELECT role FROM public.profiles
           WHERE id = 'a1620000-0000-0000-0000-000000000002'),
          'user', 'new profile role defaults to user');
UPDATE public.profiles SET role = 'admin'
WHERE id = 'a1620000-0000-0000-0000-000000000001';
SELECT throws_ok($$UPDATE public.profiles SET role = 'superadmin'
  WHERE id = 'a1620000-0000-0000-0000-000000000002'$$,
  '23514', null, 'unsupported profile roles are rejected');

INSERT INTO public.mind_maps(id, user_id, title, is_public) VALUES
    ('b1620000-0000-0000-0000-000000000001', 'a1620000-0000-0000-0000-000000000001', 'Public admin map', true),
    ('b1620000-0000-0000-0000-000000000002', 'a1620000-0000-0000-0000-000000000002', 'Private user map', false);
INSERT INTO public.nodes(id, map_id, label) VALUES
    ('c1620000-0000-0000-0000-000000000001', 'b1620000-0000-0000-0000-000000000001', 'root'),
    ('c1620000-0000-0000-0000-000000000002', 'b1620000-0000-0000-0000-000000000002', 'user root'),
    ('c1620000-0000-0000-0000-000000000003', 'b1620000-0000-0000-0000-000000000001', 'child');
INSERT INTO public.edges(map_id, source_node_id, target_node_id) VALUES
    ('b1620000-0000-0000-0000-000000000001',
     'c1620000-0000-0000-0000-000000000001', 'c1620000-0000-0000-0000-000000000003');
INSERT INTO public.chat_threads(id, map_id, user_id) VALUES
    ('e1620000-0000-0000-0000-000000000001', 'b1620000-0000-0000-0000-000000000001',
     'a1620000-0000-0000-0000-000000000001');
INSERT INTO public.chat_messages(thread_id, role, content)
VALUES ('e1620000-0000-0000-0000-000000000001', 'user', 'private chat');
INSERT INTO public.agent_memories(user_id, memory_type, content)
VALUES ('a1620000-0000-0000-0000-000000000001', 'context', 'private memory');
INSERT INTO public.generation_jobs(map_id, callback_token_hash)
VALUES ('b1620000-0000-0000-0000-000000000002', repeat('a', 64));

SELECT set_config('request.jwt.claims',
    '{"sub":"a1620000-0000-0000-0000-000000000001","role":"authenticated"}', true);
SET LOCAL ROLE authenticated;
SELECT ok(private.is_admin(), 'admin role is recognized');
SELECT is((SELECT count(*)::int FROM public.mind_maps), 0, 'admin cannot SELECT even a public/own map');
SELECT is((SELECT count(*)::int FROM public.nodes), 0, 'admin cannot SELECT nodes');
SELECT is((SELECT count(*)::int FROM public.edges), 0, 'admin cannot SELECT edges');
SELECT is((SELECT count(*)::int FROM public.chat_messages), 0, 'admin cannot SELECT chats');
SELECT is((SELECT count(*)::int FROM public.agent_memories), 0, 'admin cannot SELECT memories');
SELECT is((SELECT count(*)::int FROM public.search_maps(
    'a1620000-0000-0000-0000-000000000001', 'admin', 10)), 0,
    'admin cannot bypass table RLS via security-definer search');
SELECT is((SELECT total_generations FROM public.admin_generation_counts), 1::bigint,
    'admin sees overall generation count');
SELECT is((SELECT active_users_last_30_days FROM public.admin_active_users), 2::bigint,
    'admin sees aggregate active user count');
SELECT is((SELECT failure_rate FROM public.admin_generation_failure_rates), 0::numeric,
    'admin sees zero failure rate when no jobs have finished');
SELECT lives_ok($$INSERT INTO public.admin_audit_log(actor_id, action, target_user_id)
    VALUES ('a1620000-0000-0000-0000-000000000001', 'review_user',
            'a1620000-0000-0000-0000-000000000002')$$,
    'admin can append an audit event');
SELECT is((SELECT metadata FROM public.admin_audit_log LIMIT 1), '{}'::jsonb,
    'audit metadata defaults to empty object');
SELECT throws_ok($$DELETE FROM public.admin_audit_log$$, '42501', null,
    'admin cannot delete audit history');

RESET ROLE;
SELECT set_config('request.jwt.claims',
    '{"sub":"a1620000-0000-0000-0000-000000000002","role":"authenticated"}', true);
SET LOCAL ROLE authenticated;
SELECT is((SELECT count(*)::int FROM public.mind_maps WHERE NOT is_public), 1,
    'regular user still reads their own private map');
SELECT is((SELECT count(*)::int FROM public.admin_generation_counts), 0,
    'regular user gets no aggregate monitoring rows');
SELECT is((SELECT count(*)::int FROM public.admin_audit_log), 0,
    'regular user cannot SELECT audit events');
SELECT throws_ok($$INSERT INTO public.admin_audit_log(actor_id, action)
    VALUES ('a1620000-0000-0000-0000-000000000002', 'fake_action')$$,
    '42501', null, 'regular user cannot insert audit events');

RESET ROLE;
SELECT set_config('request.jwt.claims', '{"role":"anon"}', true);
SET LOCAL ROLE anon;
SELECT is((SELECT count(*)::int FROM public.mind_maps WHERE is_public), 1,
    'anonymous visitors can still see public maps');
SELECT throws_ok($$SELECT * FROM public.admin_generation_counts$$, '42501', null,
    'anonymous users cannot query admin aggregates');
RESET ROLE;
SELECT set_config('request.jwt.claims',
    '{"sub":"a1620000-0000-0000-0000-000000000001","role":"authenticated"}', true);
SET LOCAL ROLE authenticated;
SELECT throws_ok($$INSERT INTO public.admin_audit_log(actor_id, action)
    VALUES ('a1620000-0000-0000-0000-000000000002', 'spoof_admin')$$,
    '42501', null, 'admin cannot spoof another audit actor');
RESET ROLE;
SELECT * FROM finish();
ROLLBACK;
