-- Issue #75: authenticated collaborator access, isolation, and cascade deletion.
BEGIN;
CREATE EXTENSION IF NOT EXISTS pgtap WITH SCHEMA extensions;
SELECT plan(12);

INSERT INTO auth.users(id, email) VALUES
 ('a0000000-0000-0000-0000-000000000011','owner75@example.test'),
 ('a0000000-0000-0000-0000-000000000012','collab75@example.test'),
 ('a0000000-0000-0000-0000-000000000013','outsider75@example.test');
INSERT INTO public.mind_maps(id,user_id,title) VALUES
 ('b0000000-0000-0000-0000-000000000011','a0000000-0000-0000-0000-000000000011','Issue75 map'),
 ('b0000000-0000-0000-0000-000000000012','a0000000-0000-0000-0000-000000000011','Issue75 second map');
INSERT INTO public.nodes(id,map_id,label) VALUES
 ('c0000000-0000-0000-0000-000000000011','b0000000-0000-0000-0000-000000000011','Node 1'),
 ('c0000000-0000-0000-0000-000000000012','b0000000-0000-0000-0000-000000000012','Node 2');
INSERT INTO public.node_comments(id,map_id,node_id,user_id,content) VALUES
 ('d0000000-0000-0000-0000-000000000011','b0000000-0000-0000-0000-000000000011','c0000000-0000-0000-0000-000000000011','a0000000-0000-0000-0000-000000000011','Owner note'),
 ('d0000000-0000-0000-0000-000000000012','b0000000-0000-0000-0000-000000000012','c0000000-0000-0000-0000-000000000012','a0000000-0000-0000-0000-000000000011','Second map note');

SELECT is((SELECT status FROM public.node_comments WHERE id='d0000000-0000-0000-0000-000000000011'), 'open'::text, 'status defaults to open');
SELECT is((SELECT body FROM public.node_comments WHERE id='d0000000-0000-0000-0000-000000000011'), 'Owner note'::text, 'body reflects backend content');

SELECT set_config('request.jwt.claims','{"sub":"a0000000-0000-0000-0000-000000000012","role":"authenticated"}',true);
SET LOCAL ROLE authenticated;
SELECT is((SELECT count(*)::int FROM public.node_comments),0,'non-collaborator sees no comments');
RESET ROLE;

-- Only trusted role can issue a membership; end users cannot self-authorize.
INSERT INTO public.map_comment_collaborators(map_id,user_id)
VALUES ('b0000000-0000-0000-0000-000000000011','a0000000-0000-0000-0000-000000000012');
SET LOCAL ROLE authenticated;
SELECT is((SELECT count(*)::int FROM public.node_comments),1,'collaborator sees comments on shared map only');
SELECT lives_ok($$INSERT INTO public.node_comments(map_id,node_id,user_id,content)
VALUES ('b0000000-0000-0000-0000-000000000011','c0000000-0000-0000-0000-000000000011','a0000000-0000-0000-0000-000000000012','Collab note')$$,'collaborator can comment');
SELECT throws_ok($$INSERT INTO public.map_comment_collaborators(map_id,user_id)
VALUES ('b0000000-0000-0000-0000-000000000012','a0000000-0000-0000-0000-000000000012')$$,'42501',NULL,'collaborator cannot grant themselves access');
SELECT throws_ok($$INSERT INTO public.node_comments(map_id,node_id,user_id,content)
VALUES ('b0000000-0000-0000-0000-000000000011','c0000000-0000-0000-0000-000000000011','a0000000-0000-0000-0000-000000000011','Forged owner note')$$,'42501',NULL,'collaborator cannot impersonate owner');
RESET ROLE;

SELECT set_config('request.jwt.claims','{"sub":"a0000000-0000-0000-0000-000000000013","role":"authenticated"}',true);
SET LOCAL ROLE authenticated;
SELECT is((SELECT count(*)::int FROM public.node_comments),0,'outsider still sees no comments');
SELECT throws_ok($$INSERT INTO public.node_comments(map_id,node_id,user_id,content)
VALUES ('b0000000-0000-0000-0000-000000000011','c0000000-0000-0000-0000-000000000011','a0000000-0000-0000-0000-000000000013','Intruder')$$,'42501',NULL,'outsider cannot add comments');
RESET ROLE;

DELETE FROM public.nodes WHERE id='c0000000-0000-0000-0000-000000000011';
SELECT is((SELECT count(*)::int FROM public.node_comments WHERE map_id='b0000000-0000-0000-0000-000000000011'),0,'deleting node cascades to comments');
DELETE FROM public.mind_maps WHERE id='b0000000-0000-0000-0000-000000000012';
SELECT is((SELECT count(*)::int FROM public.node_comments WHERE map_id='b0000000-0000-0000-0000-000000000012'),0,'deleting map cascades to comments');
SELECT is((SELECT count(*)::int FROM public.map_comment_collaborators WHERE map_id='b0000000-0000-0000-0000-000000000011'),1,'other map membership remains untouched');
SELECT * FROM finish();
ROLLBACK;
