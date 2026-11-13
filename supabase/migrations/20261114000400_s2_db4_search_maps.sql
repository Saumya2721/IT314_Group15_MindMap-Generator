-- S2-DB4 / Issue #78: owner-scoped full-text search for maps and nodes.
-- Existing pg_trgm title/description indexes are retained from S2-FE5.
BEGIN;

CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Expression indexes are additive: no changes to existing API search queries.
CREATE INDEX IF NOT EXISTS idx_mind_maps_search_fts
    ON public.mind_maps USING gin (
        to_tsvector('english'::regconfig,
            coalesce(title, '') || ' ' || coalesce(description, ''))
    );
CREATE INDEX IF NOT EXISTS idx_nodes_label_fts
    ON public.nodes USING gin (
        to_tsvector('english'::regconfig, coalesce(label, ''))
    );
CREATE INDEX IF NOT EXISTS idx_nodes_label_trgm
    ON public.nodes USING gin (label gin_trgm_ops);

-- SECURITY DEFINER is needed for a single server-side RPC, but must not
-- bypass tenant isolation: caller's JWT identity must match p_user_id.
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
    ORDER BY relevance DESC, m.updated_at DESC, m.id
    LIMIT least(greatest(coalesce(p_limit, 20), 1), 100);
$fn$;

REVOKE ALL ON FUNCTION public.search_maps(uuid, text, integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.search_maps(uuid, text, integer) FROM anon;
GRANT EXECUTE ON FUNCTION public.search_maps(uuid, text, integer) TO authenticated;

COMMIT;
