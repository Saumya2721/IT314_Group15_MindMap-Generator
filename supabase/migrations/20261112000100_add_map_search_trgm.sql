-- =====================================================================
-- Migration: Trigram indexes on mind_maps for server-backed dashboard search
-- Group 15, IT314 Software Engineering (Sprint 2, Issue S2-FE5)
-- =====================================================================

BEGIN;

-- ILIKE '%term%' patterns cannot use a plain B-tree index, so enable the
-- pg_trgm extension and back title/description with GIN trigram indexes.
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE INDEX IF NOT EXISTS idx_mind_maps_title_trgm
    ON public.mind_maps USING gin (title gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_mind_maps_description_trgm
    ON public.mind_maps USING gin (COALESCE(description, '') gin_trgm_ops);

COMMIT;
