-- S3-DB3 / Issue #167: admin aggregate analytics views
-- Provides privacy-preserving aggregate views for platform operations.
-- Strictly aggregate-only: Admins cannot view raw user content.
BEGIN;

-- 1. Expanded active users & total registered users view
DROP VIEW IF EXISTS public.admin_active_users CASCADE;
CREATE VIEW public.admin_active_users
WITH (security_barrier = true)
AS
    SELECT totals.*
    FROM (
        SELECT
            (SELECT count(*)::bigint FROM public.profiles) AS total_users,
            count(DISTINCT m.user_id) FILTER (WHERE m.updated_at >= now() - interval '7 days')::bigint AS active_users_last_7_days,
            count(DISTINCT m.user_id) FILTER (WHERE m.updated_at >= now() - interval '30 days')::bigint AS active_users_last_30_days
        FROM public.mind_maps m
    ) totals
    WHERE (SELECT private.is_admin());

-- 2. Expanded generation counts view (7-day and 30-day metrics)
DROP VIEW IF EXISTS public.admin_generation_counts CASCADE;
CREATE VIEW public.admin_generation_counts
WITH (security_barrier = true)
AS
    SELECT totals.*
    FROM (
        SELECT
            count(*)::bigint AS total_generations,
            count(*) FILTER (WHERE j.created_at >= now() - interval '7 days')::bigint AS generations_last_7_days,
            count(*) FILTER (WHERE j.created_at >= now() - interval '30 days')::bigint AS generations_last_30_days,
            count(*) FILTER (WHERE j.created_at >= now() - interval '7 days' AND j.job_type != 'chat_turn')::bigint AS maps_generated_last_7_days,
            count(*) FILTER (WHERE j.created_at >= now() - interval '30 days' AND j.job_type != 'chat_turn')::bigint AS maps_generated_last_30_days
        FROM public.generation_jobs j
    ) totals
    WHERE (SELECT private.is_admin());

-- 3. Job failure rates view (re-ensure security_barrier and admin check)
DROP VIEW IF EXISTS public.admin_generation_failure_rates CASCADE;
CREATE VIEW public.admin_generation_failure_rates
WITH (security_barrier = true)
AS
    SELECT totals.*
    FROM (
        SELECT
            count(*) FILTER (WHERE j.status = 'failed')::bigint AS failed_jobs,
            count(*) FILTER (WHERE j.status = 'completed')::bigint AS completed_jobs,
            coalesce(
                round(
                    count(*) FILTER (WHERE j.status = 'failed')::numeric
                    / nullif(count(*) FILTER (WHERE j.status IN ('failed', 'completed')), 0),
                    4
                ), 0
            ) AS failure_rate
        FROM public.generation_jobs j
    ) totals
    WHERE (SELECT private.is_admin());

-- 4. Job error categories breakdown view
DROP VIEW IF EXISTS public.admin_job_error_categories CASCADE;
CREATE VIEW public.admin_job_error_categories
WITH (security_barrier = true)
AS
    SELECT
        category,
        count(*)::bigint AS count
    FROM (
        SELECT
            CASE
                WHEN lower(error_message) LIKE '%rate%' OR lower(error_message) LIKE '%429%' OR lower(error_message) LIKE '%quota%' THEN 'rate_limit'
                WHEN lower(error_message) LIKE '%timeout%' OR lower(error_message) LIKE '%timed out%' OR lower(error_message) LIKE '%deadline%' THEN 'timeout'
                WHEN lower(error_message) LIKE '%validation%' OR lower(error_message) LIKE '%invalid%' OR lower(error_message) LIKE '%schema%' THEN 'validation'
                WHEN lower(error_message) LIKE '%auth%' OR lower(error_message) LIKE '%permission%' OR lower(error_message) LIKE '%forbidden%' THEN 'auth'
                WHEN lower(error_message) LIKE '%network%' OR lower(error_message) LIKE '%connect%' THEN 'network'
                WHEN lower(error_message) LIKE '%provider%' OR lower(error_message) LIKE '%service%' OR lower(error_message) LIKE '%upstream%' THEN 'provider_error'
                ELSE 'other'
            END AS category
        FROM public.generation_jobs
        WHERE status IN ('failed', 'rejected') AND error_message IS NOT NULL
    ) categorized
    WHERE (SELECT private.is_admin())
    GROUP BY category;

-- 5. Performance latencies view (generation and chat turn)
DROP VIEW IF EXISTS public.admin_performance_latencies CASCADE;
CREATE VIEW public.admin_performance_latencies
WITH (security_barrier = true)
AS
    SELECT totals.*
    FROM (
        SELECT
            coalesce(
                round(
                    avg(EXTRACT(EPOCH FROM (j.completed_at - j.started_at)))
                    FILTER (WHERE j.status = 'completed' AND j.job_type IN ('generate', 'regenerate_full', 'regenerate_node') AND j.started_at IS NOT NULL AND j.completed_at IS NOT NULL)::numeric,
                    2
                ),
                0.0
            ) AS avg_generation_latency,
            coalesce(
                round(
                    avg(EXTRACT(EPOCH FROM (j.completed_at - j.started_at)))
                    FILTER (WHERE j.status = 'completed' AND j.job_type IN ('generate', 'regenerate_full', 'regenerate_node') AND j.started_at IS NOT NULL AND j.completed_at IS NOT NULL)::numeric,
                    2
                ),
                0.0
            ) AS avg_generation_latency_seconds,
            coalesce(
                round(
                    avg(EXTRACT(EPOCH FROM (j.completed_at - j.started_at)))
                    FILTER (WHERE j.status = 'completed' AND j.job_type = 'chat_turn' AND j.started_at IS NOT NULL AND j.completed_at IS NOT NULL)::numeric,
                    2
                ),
                0.0
            ) AS avg_chat_turn_latency,
            coalesce(
                round(
                    avg(EXTRACT(EPOCH FROM (j.completed_at - j.started_at)))
                    FILTER (WHERE j.status = 'completed' AND j.job_type = 'chat_turn' AND j.started_at IS NOT NULL AND j.completed_at IS NOT NULL)::numeric,
                    2
                ),
                0.0
            ) AS avg_chat_turn_latency_seconds
        FROM public.generation_jobs j
    ) totals
    WHERE (SELECT private.is_admin());

-- Permissions: revoke public/anon, grant authenticated and service_role
REVOKE ALL ON public.admin_active_users,
              public.admin_generation_counts,
              public.admin_generation_failure_rates,
              public.admin_job_error_categories,
              public.admin_performance_latencies
    FROM PUBLIC, anon, authenticated;

GRANT SELECT ON public.admin_active_users,
                public.admin_generation_counts,
                public.admin_generation_failure_rates,
                public.admin_job_error_categories,
                public.admin_performance_latencies TO authenticated;

GRANT SELECT ON public.admin_active_users,
                public.admin_generation_counts,
                public.admin_generation_failure_rates,
                public.admin_job_error_categories,
                public.admin_performance_latencies TO service_role;

COMMIT;
