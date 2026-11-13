-- =====================================================================
-- pgTAP tests for Issue S2-DB1: node_comments table, constraints, RLS, and integrity
-- Run with: supabase test db
-- Author: Purvi <202401172@dau.ac.in>
-- =====================================================================

begin;
create extension if not exists pgtap with schema extensions;
select plan(16);

-- ---------------------------------------------------------------------
-- 1. Schema & Column Integrity
-- ---------------------------------------------------------------------
select has_table('public', 'node_comments', 'public.node_comments table exists');

select has_column('public', 'node_comments', 'id', 'node_comments.id exists');
select has_column('public', 'node_comments', 'map_id', 'node_comments.map_id exists');
select has_column('public', 'node_comments', 'node_id', 'node_comments.node_id exists');
select has_column('public', 'node_comments', 'user_id', 'node_comments.user_id exists');
select has_column('public', 'node_comments', 'content', 'node_comments.content exists');
select has_column('public', 'node_comments', 'body', 'node_comments.body exists');
select has_column('public', 'node_comments', 'status', 'node_comments.status exists');
select has_column('public', 'node_comments', 'created_at', 'node_comments.created_at exists');
select has_column('public', 'node_comments', 'updated_at', 'node_comments.updated_at exists');

-- ---------------------------------------------------------------------
-- 2. Fixtures (Alice owns Map 1 with Node 1; Map 2 has Node 2)
-- ---------------------------------------------------------------------
insert into auth.users (id, email) values
    ('a0000000-0000-0000-0000-000000000001', 'alice@example.com'),
    ('a0000000-0000-0000-0000-000000000002', 'bob@example.com');

insert into public.mind_maps (id, user_id, title) values
    ('b0000000-0000-0000-0000-000000000001', 'a0000000-0000-0000-0000-000000000001', 'Alice private map 1'),
    ('b0000000-0000-0000-0000-000000000002', 'a0000000-0000-0000-0000-000000000001', 'Alice private map 2');

insert into public.nodes (id, map_id, label) values
    ('c0000000-0000-0000-0000-000000000001', 'b0000000-0000-0000-0000-000000000001', 'Node on map 1'),
    ('c0000000-0000-0000-0000-000000000002', 'b0000000-0000-0000-0000-000000000002', 'Node on map 2');

insert into public.node_comments (id, map_id, node_id, user_id, content) values
    ('f0000000-0000-0000-0000-000000000001',
     'b0000000-0000-0000-0000-000000000001',
     'c0000000-0000-0000-0000-000000000001',
     'a0000000-0000-0000-0000-000000000001',
     'Please expand this concept with more examples');

-- Verify default status and virtual body column
select is(
    (select status from public.node_comments where id = 'f0000000-0000-0000-0000-000000000001'),
    'open',
    'new node_comments default to status = open'
);

select is(
    (select body from public.node_comments where id = 'f0000000-0000-0000-0000-000000000001'),
    'Please expand this concept with more examples',
    'virtual body column matches content'
);

-- ---------------------------------------------------------------------
-- 3. Same-Map Integrity Check
-- ---------------------------------------------------------------------
select throws_ok(
    $$insert into public.node_comments (map_id, node_id, user_id, content)
      values ('b0000000-0000-0000-0000-000000000001', 'c0000000-0000-0000-0000-000000000002',
              'a0000000-0000-0000-0000-000000000001', 'mismatched map and node')$$,
    'P0001', null,
    'a comment cannot reference a node belonging to another map'
);

-- ---------------------------------------------------------------------
-- 4. Row Level Security (Bob tries to access Alice''s comments)
-- ---------------------------------------------------------------------
select set_config('request.jwt.claims',
    '{"sub":"a0000000-0000-0000-0000-000000000002","role":"authenticated"}', true);
set local role authenticated;

select is(
    (select count(*)::int from public.node_comments where map_id = 'b0000000-0000-0000-0000-000000000001'),
    0,
    'RLS: a user cannot read comments on another user''s map'
);

select throws_ok(
    $$insert into public.node_comments (map_id, node_id, user_id, content)
      values ('b0000000-0000-0000-0000-000000000001', 'c0000000-0000-0000-0000-000000000001',
              'a0000000-0000-0000-0000-000000000002', 'Unauthorized comment')$$,
    '42501', null,
    'RLS: a user cannot insert comments on another user''s map'
);

-- ---------------------------------------------------------------------
-- 5. Cascade Deletion (Reset role to postgres/superuser)
-- ---------------------------------------------------------------------
reset role;

delete from public.nodes where id = 'c0000000-0000-0000-0000-000000000001';

select is(
    (select count(*)::int from public.node_comments where id = 'f0000000-0000-0000-0000-000000000001'),
    0,
    'deleting the node cascades and removes associated node_comments'
);

select * from finish();
rollback;
