-- S2-DB3 / Issue #77: chat metadata and memory isolation.
BEGIN;
CREATE EXTENSION IF NOT EXISTS pgtap WITH SCHEMA extensions;
SELECT plan(22);

SELECT has_column('public', 'chat_messages', 'metadata', 'chat metadata exists');
SELECT col_type_is('public', 'chat_messages', 'metadata', 'jsonb', 'metadata is jsonb');
SELECT has_column('public', 'chat_threads', 'summary', 'existing summary is preserved');
SELECT has_column('public', 'chat_threads', 'summary_through_message_id', 'summary cursor exists');
SELECT has_table('public', 'agent_memories', 'agent memories table exists');
SELECT col_type_is('public', 'agent_memories', 'is_active', 'boolean', 'active flag is boolean');
SELECT is((SELECT count(*)::int FROM pg_constraint
           WHERE conrelid = 'public.chat_threads'::regclass AND contype = 'f'
             AND confrelid = 'public.chat_messages'::regclass), 1,
          'summary cursor has a message foreign key');
SELECT is((SELECT relrowsecurity FROM pg_class WHERE oid = 'public.agent_memories'::regclass), true,
          'agent memory RLS enabled');

-- Owner fixtures created with elevated test role, as the backend writes memory.
INSERT INTO auth.users(id, email) VALUES
    ('a0000000-0000-0000-0000-000000000031', 'db3-alice@example.com'),
    ('a0000000-0000-0000-0000-000000000032', 'db3-bob@example.com');
INSERT INTO public.mind_maps(id, user_id, title, is_public) VALUES
    ('b0000000-0000-0000-0000-000000000031', 'a0000000-0000-0000-0000-000000000031', 'Alice private', false),
    ('b0000000-0000-0000-0000-000000000032', 'a0000000-0000-0000-0000-000000000032', 'Bob public', true);
INSERT INTO public.chat_threads(id, map_id, user_id) VALUES
    ('e0000000-0000-0000-0000-000000000031', 'b0000000-0000-0000-0000-000000000031', 'a0000000-0000-0000-0000-000000000031');
INSERT INTO public.chat_messages(id, thread_id, role, content) VALUES
    ('c0000000-0000-0000-0000-000000000031', 'e0000000-0000-0000-0000-000000000031', 'user', 'hello');
SELECT is((SELECT metadata FROM public.chat_messages WHERE id = 'c0000000-0000-0000-0000-000000000031'),
          '{}'::jsonb, 'metadata defaults to an empty json object');
UPDATE public.chat_messages
SET metadata = '{"intent":"refine","cited_node_ids":[],"proposed_actions":[],"job_id":null}'::jsonb
WHERE id = 'c0000000-0000-0000-0000-000000000031';
SELECT is((SELECT metadata->>'intent' FROM public.chat_messages WHERE id = 'c0000000-0000-0000-0000-000000000031'),
          'refine', 'metadata can store intent and action context');
UPDATE public.chat_threads
SET summary = 'Earlier discussion', summary_through_message_id = 'c0000000-0000-0000-0000-000000000031'
WHERE id = 'e0000000-0000-0000-0000-000000000031';
SELECT is((SELECT summary_through_message_id FROM public.chat_threads WHERE id = 'e0000000-0000-0000-0000-000000000031'),
          'c0000000-0000-0000-0000-000000000031'::uuid, 'summary cursor saves message id');

INSERT INTO public.agent_memories(id, user_id, map_id, memory_type, content) VALUES
    ('f0000000-0000-0000-0000-000000000031', 'a0000000-0000-0000-0000-000000000031', null, 'preference', 'Alice global'),
    ('f0000000-0000-0000-0000-000000000032', 'a0000000-0000-0000-0000-000000000031', 'b0000000-0000-0000-0000-000000000031', 'context', 'Alice map'),
    ('f0000000-0000-0000-0000-000000000033', 'a0000000-0000-0000-0000-000000000032', 'b0000000-0000-0000-0000-000000000032', 'context', 'Bob map');
SELECT is((SELECT is_active FROM public.agent_memories WHERE id = 'f0000000-0000-0000-0000-000000000031'),
          true, 'agent memory defaults to active');
SELECT is((SELECT count(*)::int FROM public.agent_memories), 3, 'backend can store three memory rows');

-- Bob sees only Bob's memory, despite his map being public.
SELECT set_config('request.jwt.claims',
   '{"sub":"a0000000-0000-0000-0000-000000000032","role":"authenticated"}', true);
SET LOCAL ROLE authenticated;
SELECT is((SELECT count(*)::int FROM public.agent_memories), 1, 'Bob sees only his memory');
SELECT is((SELECT content FROM public.agent_memories LIMIT 1), 'Bob map', 'Bob cannot read Alice memory');
SELECT throws_ok($$INSERT INTO public.agent_memories(user_id,memory_type,content)
  VALUES ('a0000000-0000-0000-0000-000000000032','context','forged')$$,
  '42501', null, 'client cannot bypass backend to insert memories');
SELECT throws_ok($$UPDATE public.agent_memories SET content='forged'$$,
  '42501', null, 'client cannot edit memories directly');

-- Alice sees global and own-map memories but not Bob's.
SELECT set_config('request.jwt.claims',
   '{"sub":"a0000000-0000-0000-0000-000000000031","role":"authenticated"}', true);
SELECT is((SELECT count(*)::int FROM public.agent_memories), 2, 'Alice sees global and mapped memories');
SELECT is((SELECT count(*)::int FROM public.agent_memories WHERE map_id IS NULL), 1,
          'Alice can read her global memory');

RESET ROLE;
SELECT set_config('request.jwt.claims', '{"role":"anon"}', true);
SET LOCAL ROLE anon;
SELECT throws_ok($$SELECT id FROM public.agent_memories$$, '42501', null,
          'anonymous users cannot read memory');
RESET ROLE;

-- Deleting a map cascades its scoped memory but not global memory.
DELETE FROM public.mind_maps WHERE id = 'b0000000-0000-0000-0000-000000000031';
SELECT is((SELECT count(*)::int FROM public.agent_memories
           WHERE user_id = 'a0000000-0000-0000-0000-000000000031'), 1,
          'map removal deletes scoped memory, preserves global memory');
SELECT is((SELECT count(*)::int FROM public.agent_memories
           WHERE map_id = 'b0000000-0000-0000-0000-000000000031'), 0,
          'no memory references a deleted map');
SELECT * FROM finish();
ROLLBACK;
