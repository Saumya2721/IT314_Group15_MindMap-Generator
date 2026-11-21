-- S3-DB3 / Issue #167: admin aggregate analytics views test
BEGIN;
CREATE EXTENSION IF NOT EXISTS pgtap WITH SCHEMA extensions;
SELECT plan(19);

-- 1. View existence
SELECT has_view('public', 'admin_active_users', 'admin_active_users view exists');
SELECT has_view('public', 'admin_generation_counts', 'admin_generation_counts view exists');
SELECT has_view('public', 'admin_generation_failure_rates', 'admin_generation_failure_rates view exists');
SELECT has_view('public', 'admin_job_error_categories', 'admin_job_error_categories view exists');
SELECT has_view('public', 'admin_performance_latencies', 'admin_performance_latencies view exists');

-- 2. Column checks
SELECT has_column('public', 'admin_active_users', 'total_users', 'admin_active_users has total_users');
SELECT has_column('public', 'admin_active_users', 'active_users_last_7_days', 'admin_active_users has active_users_last_7_days');
SELECT has_column('public', 'admin_active_users', 'active_users_last_30_days', 'admin_active_users has active_users_last_30_days');

SELECT has_column('public', 'admin_generation_counts', 'total_generations', 'admin_generation_counts has total_generations');
SELECT has_column('public', 'admin_generation_counts', 'generations_last_7_days', 'admin_generation_counts has generations_last_7_days');
SELECT has_column('public', 'admin_generation_counts', 'generations_last_30_days', 'admin_generation_counts has generations_last_30_days');
SELECT has_column('public', 'admin_generation_counts', 'maps_generated_last_7_days', 'admin_generation_counts has maps_generated_last_7_days');
SELECT has_column('public', 'admin_generation_counts', 'maps_generated_last_30_days', 'admin_generation_counts has maps_generated_last_30_days');

SELECT has_column('public', 'admin_job_error_categories', 'category', 'admin_job_error_categories has category');
SELECT has_column('public', 'admin_job_error_categories', 'count', 'admin_job_error_categories has count');

SELECT has_column('public', 'admin_performance_latencies', 'avg_generation_latency', 'admin_performance_latencies has avg_generation_latency');
SELECT has_column('public', 'admin_performance_latencies', 'avg_chat_turn_latency', 'admin_performance_latencies has avg_chat_turn_latency');

-- 3. Anonymous caller cannot select views (42501: insufficient privilege)
SELECT set_config('request.jwt.claims', '{"role":"anon"}', true);
SET LOCAL ROLE anon;
SELECT throws_ok($$SELECT * FROM public.admin_performance_latencies$$, '42501', null,
    'anonymous users cannot query admin performance latencies');
SELECT throws_ok($$SELECT * FROM public.admin_job_error_categories$$, '42501', null,
    'anonymous users cannot query admin job error categories');
RESET ROLE;

SELECT * FROM finish();
ROLLBACK;
