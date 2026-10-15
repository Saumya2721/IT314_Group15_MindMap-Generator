-- Row Level Security and column privileges for the Supabase Data API roles
-- (anon / authenticated). The FastAPI backend connects as the table owner
-- (DATABASE_URL) or with the service-role key and is not affected by RLS;
-- see 03_backend_contract.test.sql for that path.
-- Run with: supabase test db
begin;
create extension if not exists pgtap with schema extensions;
select plan(26);

-- ---------------------------------------------------------------------
-- Fixtures (created as the migration owner, like the backend would)
-- ---------------------------------------------------------------------
insert into auth.users (id, email) values
    ('a0000000-0000-0000-0000-000000000001', 'alice@example.com'),
    ('a0000000-0000-0000-0000-000000000002', 'bob@example.com');

insert into public.mind_maps (id, user_id, title) values
    ('b0000000-0000-0000-0000-000000000001', 'a0000000-0000-0000-0000-000000000001', 'Alice private map'),
    ('b0000000-0000-0000-0000-000000000002', 'a0000000-0000-0000-0000-000000000001', 'Alice public map');
update public.mind_maps set is_public = true where id = 'b0000000-0000-0000-0000-000000000002';

insert into public.nodes (id, map_id, label) values
    ('c0000000-0000-0000-0000-000000000001', 'b0000000-0000-0000-0000-000000000001', 'root'),
    ('c0000000-0000-0000-0000-000000000002', 'b0000000-0000-0000-0000-000000000002', 'public root');

insert into public.chat_threads (id, map_id, user_id)
values ('e0000000-0000-0000-0000-000000000001', 'b0000000-0000-0000-0000-000000000001',
        'a0000000-0000-0000-0000-000000000001');
insert into public.chat_messages (thread_id, role, content)
values ('e0000000-0000-0000-0000-000000000001', 'user', 'raw ideas');

insert into public.generation_jobs (id, map_id, callback_token_hash)
values ('d0000000-0000-0000-0000-000000000001', 'b0000000-0000-0000-0000-000000000001', repeat('a', 64));
insert into public.seed_assets (generation_job_id, asset_type, content_text)
values ('d0000000-0000-0000-0000-000000000001', 'text', 'raw ideas');

insert into storage.objects (bucket_id, name) values
    ('seed-assets', 'a0000000-0000-0000-0000-000000000001/b0000000-0000-0000-0000-000000000001/d0000000-0000-0000-0000-000000000001/0_notes.pdf'),
    ('seed-assets', 'a0000000-0000-0000-0000-000000000002/x/y/0_bob.pdf');

-- ---------------------------------------------------------------------
-- Bob (authenticated, owns nothing)
-- ---------------------------------------------------------------------
select set_config('request.jwt.claims',
    '{"sub":"a0000000-0000-0000-0000-000000000002","role":"authenticated"}', true);
set local role authenticated;

select is((select count(*)::int from public.mind_maps where not is_public), 0,
    'a user cannot read another user''s private map');
select is((select count(*)::int from public.nodes where map_id = 'b0000000-0000-0000-0000-000000000001'), 0,
    'a user cannot read nodes of another user''s private map');
select is((select count(*)::int from public.mind_maps where is_public), 1,
    'a user can read a map its owner made public');
select throws_ok(
    $$insert into public.nodes (map_id, label)
      values ('b0000000-0000-0000-0000-000000000001', 'intruder')$$,
    '42501', null,
    'a user cannot add nodes to another user''s map'
);
select throws_ok(
    $$insert into public.nodes (map_id, label)
      values ('b0000000-0000-0000-0000-000000000002', 'intruder')$$,
    '42501', null,
    'a public map is read-only for non-owners'
);
select is((select count(*)::int from public.chat_messages), 0, 'a user cannot read another user''s chat');
select is((select count(*)::int from public.seed_assets), 0, 'a user cannot read another user''s seed assets');
select is((select count(*)::int from public.profiles), 1, 'a user only sees their own profile');
select is(
    (select array_agg(name order by name) from storage.objects where bucket_id = 'seed-assets'),
    array['a0000000-0000-0000-0000-000000000002/x/y/0_bob.pdf'],
    'a user can only read Storage objects under their own <user_id>/ folder'
);
select throws_ok(
    $$insert into storage.objects (bucket_id, name)
      values ('seed-assets', 'a0000000-0000-0000-0000-000000000002/z/0_upload.pdf')$$,
    '42501', null,
    'users cannot upload to seed-assets directly (the backend uploads with the service role)'
);

-- ---------------------------------------------------------------------
-- Alice (owner)
-- ---------------------------------------------------------------------
select set_config('request.jwt.claims',
    '{"sub":"a0000000-0000-0000-0000-000000000001","role":"authenticated"}', true);

select is((select count(*)::int from public.mind_maps), 2, 'the owner can read both of their maps');
select is((select count(*)::int from public.chat_messages), 1, 'the owner can read their chat history');
select is((select count(*)::int from public.seed_assets), 1, 'the owner can read their seed assets');
select is(
    (select status::text from public.generation_jobs where id = 'd0000000-0000-0000-0000-000000000001'),
    'pending',
    'the owner can read job status/progress'
);
select throws_ok(
    $$select callback_token_hash from public.generation_jobs$$,
    '42501', null,
    'the owner cannot read the backend-only callback_token_hash'
);
select throws_ok(
    $$insert into public.generation_jobs (map_id) values ('b0000000-0000-0000-0000-000000000002')$$,
    '42501', null,
    'users cannot create generation jobs directly (bypassing backend rate limiting)'
);
select throws_ok(
    $$insert into public.chat_messages (thread_id, role, content)
      values ('e0000000-0000-0000-0000-000000000001', 'assistant', 'forged')$$,
    '42501', null,
    'users cannot forge chat messages'
);
select lives_ok(
    $$insert into public.nodes (map_id, label, pos_x, pos_y)
      values ('b0000000-0000-0000-0000-000000000001', 'added on canvas', 10, 20)$$,
    'the owner can add a node to their map (canvas editing)'
);
select lives_ok(
    $$update public.nodes set pos_x = 100, pos_y = 200
      where id = 'c0000000-0000-0000-0000-000000000001'$$,
    'the owner can move a node'
);
select throws_ok(
    $$update public.nodes set source_job_id = null where id = 'c0000000-0000-0000-0000-000000000001'$$,
    '42501', null,
    'users cannot rewrite node provenance (source_job_id)'
);
select throws_ok(
    $$update public.mind_maps set user_id = 'a0000000-0000-0000-0000-000000000002'
      where id = 'b0000000-0000-0000-0000-000000000001'$$,
    '42501', null,
    'users cannot change a map''s owner column'
);
select lives_ok(
    $$update public.profiles set name = 'Alice' where id = 'a0000000-0000-0000-0000-000000000001'$$,
    'a user can update their own display name'
);

-- ---------------------------------------------------------------------
-- Anonymous visitors
-- ---------------------------------------------------------------------
reset role;
select set_config('request.jwt.claims', '{"role":"anon"}', true);
set local role anon;

select is((select count(*)::int from public.mind_maps where not is_public), 0,
    'anonymous visitors cannot read a private map');
select is((select count(*)::int from public.nodes), 1,
    'anonymous visitors can read only the nodes of public maps');
select throws_ok(
    $$select id from public.generation_jobs$$,
    '42501', null,
    'anonymous visitors have no access to generation jobs'
);
select throws_ok(
    $$select id from public.profiles$$,
    '42501', null,
    'anonymous visitors have no access to profiles'
);

reset role;
select * from finish();
rollback;
