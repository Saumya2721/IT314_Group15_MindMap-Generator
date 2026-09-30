begin;
create extension if not exists pgtap with schema extensions;
select plan(5);

insert into auth.users (id, email) values
    ('a0000000-0000-0000-0000-000000000001', 'alice@example.com'),
    ('a0000000-0000-0000-0000-000000000002', 'bob@example.com');

insert into public.mind_maps (id, user_id, title)
values ('b0000000-0000-0000-0000-000000000001',
        'a0000000-0000-0000-0000-000000000001', 'Alice map');

insert into public.nodes (id, map_id, label)
values ('c0000000-0000-0000-0000-000000000001',
        'b0000000-0000-0000-0000-000000000001', 'root');

-- Act as Bob, who owns nothing.
select set_config('request.jwt.claim.sub', 'a0000000-0000-0000-0000-000000000002', true);
select set_config('request.jwt.claims',
    '{"sub":"a0000000-0000-0000-0000-000000000002","role":"authenticated"}', true);
set local role authenticated;

select is((select count(*)::int from public.mind_maps), 0,
    'a user cannot read another user''s map');
select is((select count(*)::int from public.nodes), 0,
    'a user cannot read nodes of another user''s map');

select throws_ok(
    $$insert into public.nodes (map_id, label)
      values ('b0000000-0000-0000-0000-000000000001', 'intruder')$$,
    '42501', null,
    'a user cannot add nodes to another user''s map'
);

-- Act as Alice, the owner.
select set_config('request.jwt.claim.sub', 'a0000000-0000-0000-0000-000000000001', true);
select set_config('request.jwt.claims',
    '{"sub":"a0000000-0000-0000-0000-000000000001","role":"authenticated"}', true);

select is((select count(*)::int from public.mind_maps), 1, 'the owner can read their map');

reset role;
set local role anon;
select is((select count(*)::int from public.mind_maps), 0,
    'anonymous visitors cannot read any map');

reset role;
select * from finish();
rollback;
