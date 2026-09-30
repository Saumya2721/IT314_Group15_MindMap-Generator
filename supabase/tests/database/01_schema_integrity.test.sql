begin;
create extension if not exists pgtap with schema extensions;
select plan(6);

insert into auth.users (id, email)
values ('a0000000-0000-0000-0000-000000000001', 'alice@example.com');

insert into public.mind_maps (id, user_id) values
    ('b0000000-0000-0000-0000-000000000001', 'a0000000-0000-0000-0000-000000000001'),
    ('b0000000-0000-0000-0000-000000000002', 'a0000000-0000-0000-0000-000000000001');

insert into public.nodes (id, map_id, label) values
    ('c0000000-0000-0000-0000-000000000001', 'b0000000-0000-0000-0000-000000000001', 'root'),
    ('c0000000-0000-0000-0000-000000000002', 'b0000000-0000-0000-0000-000000000001', 'child'),
    ('c0000000-0000-0000-0000-000000000003', 'b0000000-0000-0000-0000-000000000002', 'other map');

insert into public.edges (map_id, source_node_id, target_node_id)
values ('b0000000-0000-0000-0000-000000000001',
        'c0000000-0000-0000-0000-000000000001',
        'c0000000-0000-0000-0000-000000000002');

select is(
    (select email from public.users where id = 'a0000000-0000-0000-0000-000000000001'),
    'alice@example.com',
    'signing up creates a profile row'
);

select throws_ok(
    $$insert into public.edges (map_id, source_node_id, target_node_id)
      values ('b0000000-0000-0000-0000-000000000001',
              'c0000000-0000-0000-0000-000000000001',
              'c0000000-0000-0000-0000-000000000003')$$,
    'P0001', null,
    'an edge pointing at a node from another map is rejected'
);

select throws_ok(
    $$insert into public.edges (map_id, source_node_id, target_node_id)
      values ('b0000000-0000-0000-0000-000000000001',
              'c0000000-0000-0000-0000-000000000001',
              'c0000000-0000-0000-0000-000000000002')$$,
    '23505', null,
    'a duplicate unlabeled edge is rejected'
);

insert into public.generation_jobs (id, map_id, job_type)
values ('d0000000-0000-0000-0000-000000000001', 'b0000000-0000-0000-0000-000000000001', 'generate');

select throws_ok(
    $$insert into public.generation_jobs (map_id, job_type)
      values ('b0000000-0000-0000-0000-000000000001', 'regenerate_full')$$,
    '23505', null,
    'a second active full-map job on the same map is rejected'
);

select lives_ok(
    $$insert into public.generation_jobs (map_id, job_type, target_node_id)
      values ('b0000000-0000-0000-0000-000000000001', 'regenerate_node',
              'c0000000-0000-0000-0000-000000000002')$$,
    'node-level regeneration can run alongside a full-map job'
);

insert into public.embeddings
    (generation_job_id, map_id, chunk_text, embedding, embedding_model, source)
values
    ('d0000000-0000-0000-0000-000000000001', 'b0000000-0000-0000-0000-000000000001',
     'a chunk', array_fill(0.1::real, array[1536])::vector, 'test-model', 'uploaded_seed');

select is(
    (select count(*)::int from public.match_embeddings(
        array_fill(0.1::real, array[1536])::vector,
        'd0000000-0000-0000-0000-000000000001')),
    1,
    'match_embeddings returns the stored chunk for its job'
);

select * from finish();
rollback;
