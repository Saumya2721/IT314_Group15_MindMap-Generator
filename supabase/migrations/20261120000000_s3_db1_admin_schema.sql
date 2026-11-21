-- S3-DB1 / Issue #162: administrative role, suspension marker, audit trail,
-- and aggregate-only monitoring. Existing ownership rules remain intact.
BEGIN;

ALTER TABLE public.profiles
    ADD COLUMN role text NOT NULL DEFAULT 'user',
    ADD COLUMN suspended_at timestamptz,
    ADD CONSTRAINT chk_profiles_role CHECK (role IN ('user', 'admin'));

-- The existing profiles INSERT grant is table-wide. Limit it to safe
-- columns so clients cannot register themselves as admins or self-suspend.
REVOKE INSERT ON public.profiles FROM authenticated;
GRANT INSERT (id, name) ON public.profiles TO authenticated;
-- Only name was updateable previously; explicitly revoke any whole-table
-- UPDATE grant to keep role and suspended_at server-controlled.
REVOKE UPDATE ON public.profiles FROM authenticated;
GRANT UPDATE (name) ON public.profiles TO authenticated;

-- The function runs under the DB owner to read role without exposing other
-- profiles or creating recursive RLS policies. auth.uid() remains the caller.
CREATE OR REPLACE FUNCTION private.is_admin()
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = ''
AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.profiles p
        WHERE p.id = (SELECT auth.uid())
          AND p.role = 'admin'
          AND p.suspended_at IS NULL
    );
$$;
REVOKE ALL ON FUNCTION private.is_admin() FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION private.is_admin() TO authenticated;

-- Existing permissive SELECT rules include publicly shared maps, so an
-- additional RESTRICTIVE rule is necessary: a permissive rule alone could
-- still expose private content to an admin. This blocks even own/public
-- content for authenticated admin sessions, without changing anon access.
CREATE POLICY mind_maps_admin_no_select ON public.mind_maps
    AS RESTRICTIVE FOR SELECT TO authenticated
    USING (NOT (SELECT private.is_admin()));
CREATE POLICY nodes_admin_no_select ON public.nodes
    AS RESTRICTIVE FOR SELECT TO authenticated
    USING (NOT (SELECT private.is_admin()));
CREATE POLICY edges_admin_no_select ON public.edges
    AS RESTRICTIVE FOR SELECT TO authenticated
    USING (NOT (SELECT private.is_admin()));
CREATE POLICY chat_messages_admin_no_select ON public.chat_messages
    AS RESTRICTIVE FOR SELECT TO authenticated
    USING (NOT (SELECT private.is_admin()));
CREATE POLICY agent_memories_admin_no_select ON public.agent_memories
    AS RESTRICTIVE FOR SELECT TO authenticated
    USING (NOT (SELECT private.is_admin()));

CREATE TABLE public.admin_audit_log (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    actor_id uuid NOT NULL REFERENCES auth.users(id),
    action text NOT NULL CHECK (length(btrim(action)) > 0),
    target_user_id uuid REFERENCES auth.users(id),
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_admin_audit_log_actor_created
    ON public.admin_audit_log (actor_id, created_at DESC);
CREATE INDEX idx_admin_audit_log_created
    ON public.admin_audit_log (created_at DESC);
ALTER TABLE public.admin_audit_log ENABLE ROW LEVEL SECURITY;

CREATE POLICY admin_audit_log_insert ON public.admin_audit_log
    FOR INSERT TO authenticated
    WITH CHECK (actor_id = (SELECT auth.uid()) AND (SELECT private.is_admin()));
CREATE POLICY admin_audit_log_select ON public.admin_audit_log
    FOR SELECT TO authenticated
    USING ((SELECT private.is_admin()));

REVOKE ALL ON public.admin_audit_log FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT ON public.admin_audit_log TO authenticated;
GRANT ALL ON public.admin_audit_log TO service_role;
-- No UPDATE/DELETE permissions: the audit trail is append-only for clients.

-- Only aggregate outputs are exposed. These views are intentionally owned
-- by the migration owner (who can read source tables despite user RLS).
-- The admin gate uses auth.uid(), so non-admins get no rows.
CREATE VIEW public.admin_generation_counts
WITH (security_barrier = true)
AS
    SELECT totals.*
    FROM (
        SELECT count(*)::bigint AS total_generations,
               count(*) FILTER (WHERE j.created_at >= now() - interval '30 days')::bigint
                   AS generations_last_30_days
        FROM public.generation_jobs j
    ) totals
    WHERE (SELECT private.is_admin());

-- Active means the user created or updated at least one map in 30 days.
CREATE VIEW public.admin_active_users
WITH (security_barrier = true)
AS
    SELECT totals.*
    FROM (
        SELECT count(DISTINCT m.user_id)::bigint AS active_users_last_30_days
        FROM public.mind_maps m
        WHERE m.updated_at >= now() - interval '30 days'
    ) totals
    WHERE (SELECT private.is_admin());

-- A failed generation is measured against completed + failed jobs;
-- pending, running and rejected jobs are excluded from the rate.
CREATE VIEW public.admin_generation_failure_rates
WITH (security_barrier = true)
AS
    SELECT totals.*
    FROM (
        SELECT count(*) FILTER (WHERE j.status = 'failed')::bigint AS failed_jobs,
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

REVOKE ALL ON public.admin_generation_counts,
              public.admin_active_users,
              public.admin_generation_failure_rates
    FROM PUBLIC, anon, authenticated;
GRANT SELECT ON public.admin_generation_counts,
                public.admin_active_users,
                public.admin_generation_failure_rates TO authenticated;
GRANT SELECT ON public.admin_generation_counts,
                public.admin_active_users,
                public.admin_generation_failure_rates TO service_role;

-- search_maps() is SECURITY DEFINER and otherwise bypasses table RLS.
-- Preserve owner-scoped search for normal users but explicitly deny admins.
CREATE OR REPLACE FUNCTION public.search_maps(
    p_user_id uuid,
    p_query text,
    p_limit integer DEFAULT 20
)
RETURNS TABLE (
    id uuid,
    title varchar(255),
    description text,
    snippet text,
    relevance real
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = ''
AS $fn$
    WITH query_input AS (
        SELECT btrim(coalesce(p_query, '')) AS term,
               websearch_to_tsquery('english'::regconfig, btrim(coalesce(p_query, ''))) AS tsq
    ), matches AS (
        SELECT m.id, m.title, m.description, m.updated_at, q.term, q.tsq,
               to_tsvector('english'::regconfig,
                   coalesce(m.title, '') || ' ' || coalesce(m.description, '')) AS map_vec,
               EXISTS (
                   SELECT 1 FROM public.nodes n
                   WHERE n.map_id = m.id
                     AND (
                       to_tsvector('english'::regconfig, coalesce(n.label,'')) @@ q.tsq
                       OR strpos(lower(n.label), lower(q.term)) > 0
                     )
               ) AS node_match
        FROM public.mind_maps m CROSS JOIN query_input q
        WHERE m.user_id = p_user_id
          AND q.term <> ''
    )
    SELECT m.id, m.title, m.description,
           CASE
             WHEN m.map_vec @@ m.tsq THEN
               ts_headline('english'::regconfig,
                   coalesce(m.description, m.title)::text,
                   m.tsq, 'StartSel=[[, StopSel=]], MaxWords=22, MinWords=5')
             ELSE left(coalesce(m.description, m.title)::text, 180)
           END AS snippet,
           (ts_rank(m.map_vec, m.tsq) + CASE WHEN m.node_match THEN 0.1 ELSE 0 END)::real AS relevance
    FROM matches m
    WHERE (m.map_vec @@ m.tsq OR m.node_match OR
           strpos(lower(m.title), lower(m.term)) > 0 OR
           strpos(lower(coalesce(m.description, '')), lower(m.term)) > 0)
      AND auth.uid() = p_user_id
      AND NOT (SELECT private.is_admin())
    ORDER BY relevance DESC, m.updated_at DESC, m.id
    LIMIT least(greatest(coalesce(p_limit, 20), 1), 100);
$fn$;

COMMIT;
