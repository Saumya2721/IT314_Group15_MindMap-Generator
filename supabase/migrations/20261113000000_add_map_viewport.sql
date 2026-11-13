-- =====================================================================
-- Migration: Persist editor viewport (zoom + pan) on mind_maps
-- Group 15, IT314 Software Engineering (Sprint 2, Issue S2-FE5)
-- =====================================================================

BEGIN;

ALTER TABLE public.mind_maps
    ADD COLUMN IF NOT EXISTS view_x DOUBLE PRECISION DEFAULT 0;

ALTER TABLE public.mind_maps
    ADD COLUMN IF NOT EXISTS view_y DOUBLE PRECISION DEFAULT 0;

ALTER TABLE public.mind_maps
    ADD COLUMN IF NOT EXISTS view_zoom DOUBLE PRECISION DEFAULT 1;

-- Authenticated users (the frontend client role) may patch viewport fields
-- on maps they own via the PATCH /maps/{map_id} endpoint (UpdateMap schema).
GRANT UPDATE (view_x, view_y, view_zoom) ON public.mind_maps TO authenticated;

COMMIT;
