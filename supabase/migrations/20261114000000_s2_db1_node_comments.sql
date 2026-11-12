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

-- 3. Index on (node_id, status) for fast query of open / resolved comments
CREATE INDEX IF NOT EXISTS idx_node_comments_node_status
    ON public.node_comments (node_id, status);

COMMENT ON COLUMN public.node_comments.status IS 'Status of comment/refinement request: open or resolved (S2-DB1)';
COMMENT ON COLUMN public.node_comments.body IS 'Virtual alias for content to maintain compatibility with S2-DB1 specification';

COMMIT;
