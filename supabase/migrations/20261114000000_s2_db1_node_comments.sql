-- =====================================================================
-- Migration: S2-DB1 Node Comments Enhancements & Status Tracking
-- Group 15, IT314 Software Engineering (Sprint 2, Issue S2-DB1 / US-06)
-- Author: Purvi <202401172@dau.ac.in>
-- =====================================================================

BEGIN;

-- 1. Ensure status column exists on node_comments with constraint
ALTER TABLE public.node_comments
    ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'open';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'chk_node_comment_status'
          AND conrelid = 'public.node_comments'::regclass
    ) THEN
        ALTER TABLE public.node_comments
            ADD CONSTRAINT chk_node_comment_status
            CHECK (status IN ('open', 'resolved'));
    END IF;
END $$;

-- 2. Ensure body column alias exists for compatibility with Issue S2-DB1
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'node_comments'
          AND column_name = 'body'
    ) THEN
        ALTER TABLE public.node_comments
            ADD COLUMN body TEXT GENERATED ALWAYS AS (content) STORED;
    END IF;
END $$;

-- 3. Ensure same-map integrity function has SECURITY DEFINER so non-owner RLS checks properly raise 42501
CREATE OR REPLACE FUNCTION public.check_node_comment_same_map()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
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

-- 4. Index on (node_id, status) for fast query of open / resolved comments
CREATE INDEX IF NOT EXISTS idx_node_comments_node_status
    ON public.node_comments (node_id, status);

COMMENT ON COLUMN public.node_comments.status IS 'Status of comment/refinement request: open or resolved (S2-DB1)';
COMMENT ON COLUMN public.node_comments.body IS 'Virtual alias for content to maintain compatibility with S2-DB1 specification';

COMMIT;
