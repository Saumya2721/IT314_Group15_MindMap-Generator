BEGIN;

ALTER TABLE public.generation_jobs ADD COLUMN base_map_version bigint;

ALTER TABLE public.agent_memories ADD COLUMN updated_at timestamptz;
UPDATE public.agent_memories SET updated_at = created_at;
ALTER TABLE public.agent_memories
    ALTER COLUMN updated_at SET DEFAULT now(),
    ALTER COLUMN updated_at SET NOT NULL;

UPDATE public.agent_memories
SET importance = CASE WHEN importance < 0 THEN 0 ELSE 1 END
WHERE NOT (importance BETWEEN 0 AND 1);
ALTER TABLE public.agent_memories
    DROP CONSTRAINT IF EXISTS chk_agent_memories_importance,
    ADD CONSTRAINT chk_agent_memories_importance CHECK (importance BETWEEN 0 AND 1);

WITH ranked AS (
    SELECT id, row_number() OVER (
        PARTITION BY user_id, lower(btrim(content))
        ORDER BY importance DESC, created_at DESC, id
    ) AS duplicate_rank
    FROM public.agent_memories
    WHERE is_active
)
UPDATE public.agent_memories m
SET is_active = false, disabled_at = COALESCE(m.disabled_at, now()), updated_at = now()
FROM ranked
WHERE m.id = ranked.id AND ranked.duplicate_rank > 1;

CREATE UNIQUE INDEX uq_agent_memories_active_content
    ON public.agent_memories (user_id, md5(lower(btrim(content))))
    WHERE is_active;

ALTER TABLE public.admin_audit_log
    DROP CONSTRAINT admin_audit_log_actor_id_fkey,
    DROP CONSTRAINT admin_audit_log_target_user_id_fkey;

CREATE TABLE public.account_deletion_requests (
    user_id uuid PRIMARY KEY,
    actor_id uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    map_ids uuid[] NOT NULL DEFAULT '{}',
    storage_cleaned_at timestamptz,
    vector_attempt integer NOT NULL DEFAULT 0,
    vectors_cleaned_at timestamptz,
    completed_at timestamptz
);
ALTER TABLE public.account_deletion_requests ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.account_deletion_requests FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON public.account_deletion_requests TO service_role;

CREATE FUNCTION private.is_active_account()
RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = ''
AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.profiles
        WHERE id = (SELECT auth.uid()) AND suspended_at IS NULL
    );
$$;
REVOKE ALL ON FUNCTION private.is_active_account() FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION private.is_active_account() TO authenticated;

CREATE POLICY agent_memories_active_account ON public.agent_memories
    AS RESTRICTIVE FOR ALL TO authenticated
    USING ((SELECT private.is_active_account()))
    WITH CHECK ((SELECT private.is_active_account()));

CREATE POLICY seed_storage_active_account ON storage.objects
    AS RESTRICTIVE FOR ALL TO authenticated
    USING (bucket_id <> 'seed-assets' OR (SELECT private.is_active_account()))
    WITH CHECK (bucket_id <> 'seed-assets' OR (SELECT private.is_active_account()));

CREATE OR REPLACE VIEW public.admin_generation_counts
WITH (security_barrier = true)
AS
WITH completed_maps AS (
    SELECT map_id, min(completed_at) AS first_completed_at
    FROM public.generation_jobs
    WHERE job_type = 'generate' AND status = 'completed'
    GROUP BY map_id
),
daily AS (
    SELECT day::date AS day, count(c.map_id) AS count
    FROM generate_series(
        date_trunc('day', now() AT TIME ZONE 'UTC') - interval '6 days',
        date_trunc('day', now() AT TIME ZONE 'UTC'),
        interval '1 day'
    ) AS day
    LEFT JOIN completed_maps c ON (c.first_completed_at AT TIME ZONE 'UTC')::date = day::date
    GROUP BY day
)
SELECT
    count(*) AS total_generations,
    count(*) FILTER (WHERE created_at >= now() - interval '7 days') AS generations_last_7_days,
    count(*) FILTER (WHERE created_at >= now() - interval '30 days') AS generations_last_30_days,
    (SELECT count(*) FROM completed_maps WHERE first_completed_at >= now() - interval '7 days')
        AS maps_generated_last_7_days,
    (SELECT count(*) FROM completed_maps WHERE first_completed_at >= now() - interval '30 days')
        AS maps_generated_last_30_days,
    (SELECT count(*) FROM public.mind_maps) AS total_maps,
    (SELECT jsonb_agg(jsonb_build_object('date', day, 'count', count) ORDER BY day) FROM daily)
        AS maps_created_per_day
FROM public.generation_jobs
HAVING (SELECT private.is_admin());

COMMIT;
