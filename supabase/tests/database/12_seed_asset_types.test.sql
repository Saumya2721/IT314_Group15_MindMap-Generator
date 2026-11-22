-- S3-DB3 / Issue #164: supported asset types and their storage requirements.
BEGIN;
CREATE EXTENSION IF NOT EXISTS pgtap WITH SCHEMA extensions;
SET LOCAL search_path = public, extensions;
SELECT plan(14);

INSERT INTO auth.users (id, email)
VALUES ('a1640000-0000-0000-0000-000000000001', 'seed-assets@example.invalid');

INSERT INTO public.mind_maps (id, user_id)
VALUES ('b1640000-0000-0000-0000-000000000001', 'a1640000-0000-0000-0000-000000000001');

INSERT INTO public.generation_jobs (id, map_id, job_type)
VALUES ('d1640000-0000-0000-0000-000000000001', 'b1640000-0000-0000-0000-000000000001', 'generate');

SELECT lives_ok(
    $$INSERT INTO public.seed_assets (generation_job_id, asset_type, file_name, storage_key)
      VALUES ('d1640000-0000-0000-0000-000000000001', 'docx', 'notes.docx', 'test/notes.docx')$$,
    'DOCX seeds accept a file name and storage key'
);
SELECT lives_ok(
    $$INSERT INTO public.seed_assets (generation_job_id, asset_type, file_name, storage_key)
      VALUES ('d1640000-0000-0000-0000-000000000001', 'pptx', 'slides.pptx', 'test/slides.pptx')$$,
    'PPTX seeds accept a file name and storage key'
);
SELECT lives_ok(
    $$INSERT INTO public.seed_assets (generation_job_id, asset_type, content_text)
      VALUES ('d1640000-0000-0000-0000-000000000001', 'url', 'https://example.invalid/article')$$,
    'URL seeds can be stored without a file'
);
SELECT lives_ok(
    $$INSERT INTO public.seed_assets (generation_job_id, asset_type, file_name, storage_key)
      VALUES ('d1640000-0000-0000-0000-000000000001', 'pdf', 'notes.pdf', 'test/notes.pdf')$$,
    'PDF seeds still accept a file name and storage key'
);
SELECT lives_ok(
    $$INSERT INTO public.seed_assets (generation_job_id, asset_type, file_name, storage_key)
      VALUES ('d1640000-0000-0000-0000-000000000001', 'image', 'figure.png', 'test/figure.png')$$,
    'image seeds still accept a file name and storage key'
);
SELECT lives_ok(
    $$INSERT INTO public.seed_assets (generation_job_id, asset_type, content_text)
      VALUES ('d1640000-0000-0000-0000-000000000001', 'text', 'free text seed')$$,
    'text seeds can still be stored without a file'
);

SELECT throws_ok(
    $$INSERT INTO public.seed_assets (generation_job_id, asset_type, file_name, storage_key)
      VALUES ('d1640000-0000-0000-0000-000000000001', 'zip', 'notes.zip', 'test/notes.zip')$$,
    '23514', NULL,
    'unsupported asset types are rejected'
);
SELECT throws_ok(
    $$INSERT INTO public.seed_assets (generation_job_id, asset_type)
      VALUES ('d1640000-0000-0000-0000-000000000001', NULL)$$,
    '23502', NULL,
    'an asset type is required'
);
SELECT throws_ok(
    $$INSERT INTO public.seed_assets (generation_job_id, asset_type, file_name)
      VALUES ('d1640000-0000-0000-0000-000000000001', 'docx', 'notes.docx')$$,
    '23514', NULL,
    'DOCX seeds require a storage key'
);
SELECT throws_ok(
    $$INSERT INTO public.seed_assets (generation_job_id, asset_type, storage_key)
      VALUES ('d1640000-0000-0000-0000-000000000001', 'docx', 'test/notes.docx')$$,
    '23514', NULL,
    'DOCX seeds require a file name'
);
SELECT throws_ok(
    $$INSERT INTO public.seed_assets (generation_job_id, asset_type, file_name)
      VALUES ('d1640000-0000-0000-0000-000000000001', 'pptx', 'slides.pptx')$$,
    '23514', NULL,
    'PPTX seeds require a storage key'
);
SELECT throws_ok(
    $$INSERT INTO public.seed_assets (generation_job_id, asset_type, storage_key)
      VALUES ('d1640000-0000-0000-0000-000000000001', 'pptx', 'test/slides.pptx')$$,
    '23514', NULL,
    'PPTX seeds require a file name'
);
SELECT throws_ok(
    $$INSERT INTO public.seed_assets (generation_job_id, asset_type, file_name, storage_key, content_text)
      VALUES ('d1640000-0000-0000-0000-000000000001', 'url', 'article.html',
              'test/article.html', 'https://example.invalid/article')$$,
    '23514', NULL,
    'URL seeds reject a storage key'
);
SELECT throws_ok(
    $$INSERT INTO public.seed_assets (generation_job_id, asset_type, file_name, storage_key)
      VALUES ('d1640000-0000-0000-0000-000000000001', 'text', 'notes.txt', 'test/notes.txt')$$,
    '23514', NULL,
    'text seeds still reject a storage key'
);

SELECT * FROM finish();
ROLLBACK;
