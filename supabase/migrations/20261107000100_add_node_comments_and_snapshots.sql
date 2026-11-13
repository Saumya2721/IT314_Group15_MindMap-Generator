-- =====================================================================
-- Migration: Add node comments, map snapshots, and active-node-job index
-- Group 15, IT314 Software Engineering (Sprint 2, Issue S2-BE2)
-- =====================================================================

BEGIN;

-- ---------------------------------------------------------------------
-- 1. NODE COMMENTS
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.node_comments (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    map_id      UUID NOT NULL REFERENCES public.mind_maps(id) ON DELETE CASCADE,
    node_id     UUID NOT NULL REFERENCES public.nodes(id) ON DELETE CASCADE,
    user_id     UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    content     TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT chk_node_comment_content_non_empty CHECK (LENGTH(BTRIM(content)) > 0)
);

CREATE INDEX IF NOT EXISTS idx_node_comments_node_id ON public.node_comments (node_id);
CREATE INDEX IF NOT EXISTS idx_node_comments_map_id ON public.node_comments (map_id);

CREATE TRIGGER trg_node_comments_updated_at
    BEFORE UPDATE ON public.node_comments
    FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

-- Same-map integrity trigger for node comments
CREATE OR REPLACE FUNCTION public.check_node_comment_same_map()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = ''
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM public.nodes WHERE id = NEW.node_id AND map_id = NEW.map_id
    ) THEN
        RAISE EXCEPTION 'node_comment %: node_id % does not belong to map %',
            NEW.id, NEW.node_id, NEW.map_id;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_node_comments_same_map ON public.node_comments;
CREATE TRIGGER trg_node_comments_same_map
    BEFORE INSERT OR UPDATE OF node_id, map_id ON public.node_comments
    FOR EACH ROW EXECUTE FUNCTION public.check_node_comment_same_map();

ALTER TABLE public.node_comments ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS node_comments_select_own ON public.node_comments;
CREATE POLICY node_comments_select_own ON public.node_comments
    FOR SELECT TO authenticated
    USING (
        EXISTS (
            SELECT 1 FROM public.mind_maps m
            WHERE m.id = node_comments.map_id AND m.user_id = auth.uid()
        )
    );

DROP POLICY IF EXISTS node_comments_all_own ON public.node_comments;
CREATE POLICY node_comments_all_own ON public.node_comments
    FOR ALL TO authenticated
    USING (
        EXISTS (
            SELECT 1 FROM public.mind_maps m
            WHERE m.id = node_comments.map_id AND m.user_id = auth.uid()
        )
    )
    WITH CHECK (
        EXISTS (
            SELECT 1 FROM public.mind_maps m
            WHERE m.id = node_comments.map_id AND m.user_id = auth.uid()
        )
    );

-- ---------------------------------------------------------------------
-- 2. MAP SNAPSHOTS (versioned history before branch / full regeneration)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.map_snapshots (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    map_id          UUID NOT NULL REFERENCES public.mind_maps(id) ON DELETE CASCADE,
    trigger_job_id  UUID REFERENCES public.generation_jobs(id) ON DELETE SET NULL,
    target_node_id  UUID REFERENCES public.nodes(id) ON DELETE SET NULL,
    reason          VARCHAR(50) NOT NULL DEFAULT 'pre_regeneration',
    nodes_snapshot  JSONB NOT NULL,
    edges_snapshot  JSONB NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_map_snapshots_map_id ON public.map_snapshots (map_id, created_at DESC);

ALTER TABLE public.map_snapshots ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS map_snapshots_select_own ON public.map_snapshots;
CREATE POLICY map_snapshots_select_own ON public.map_snapshots
    FOR SELECT TO authenticated
    USING (
        EXISTS (
            SELECT 1 FROM public.mind_maps m
            WHERE m.id = map_snapshots.map_id AND m.user_id = auth.uid()
        )
    );

-- ---------------------------------------------------------------------
-- 3. ACTIVE NODE-LEVEL REGENERATION INDEX
-- At most one active node-level regeneration per (map_id, target_node_id)
-- ---------------------------------------------------------------------
CREATE UNIQUE INDEX IF NOT EXISTS uq_generation_jobs_active_node
    ON public.generation_jobs (map_id, target_node_id)
    WHERE status IN ('pending', 'running') AND job_type = 'regenerate_node';

COMMIT;
