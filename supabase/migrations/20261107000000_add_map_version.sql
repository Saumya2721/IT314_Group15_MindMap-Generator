-- =====================================================================
-- Migration: Add version column to mind_maps for optimistic concurrency control
-- Group 15, IT314 Software Engineering (Sprint 2, Issue S2-BE1)
-- =====================================================================

BEGIN;

ALTER TABLE public.mind_maps
    ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1;

COMMENT ON COLUMN public.mind_maps.version IS
    'Optimistic concurrency control version counter, incremented on every graph or map modification.';

COMMIT;
