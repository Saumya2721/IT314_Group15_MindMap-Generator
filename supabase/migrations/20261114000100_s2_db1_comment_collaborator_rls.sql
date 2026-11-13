-- Issue #75: grant comment access only to explicitly authorized map collaborators.
-- This ACL is managed by the trusted backend/database owner, never by end users.
BEGIN;

CREATE SCHEMA IF NOT EXISTS private;
GRANT USAGE ON SCHEMA private TO authenticated;

CREATE TABLE IF NOT EXISTS public.map_comment_collaborators (
    map_id uuid NOT NULL REFERENCES public.mind_maps(id) ON DELETE CASCADE,
    user_id uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (map_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_map_comment_collaborators_user_id
    ON public.map_comment_collaborators(user_id);
ALTER TABLE public.map_comment_collaborators ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.map_comment_collaborators FROM PUBLIC, anon, authenticated;

-- SECURITY DEFINER reads the ACL without requiring direct client access to it.
-- A membership can only be inserted by a trusted database/backend role.
CREATE OR REPLACE FUNCTION private.is_map_comment_collaborator(p_map_id uuid)
RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = ''
AS $$
    SELECT EXISTS (
        SELECT 1
        FROM public.map_comment_collaborators mc
        WHERE mc.map_id = p_map_id
          AND mc.user_id = (SELECT auth.uid())
          AND EXISTS (SELECT 1 FROM public.mind_maps m WHERE m.id = mc.map_id)
    );
$$;
REVOKE ALL ON FUNCTION private.is_map_comment_collaborator(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION private.is_map_comment_collaborator(uuid) TO authenticated;

-- Existing owner policies remain unchanged. Collaborators may read the map's
-- comments, and can create/update/delete only comments attributed to themselves.
GRANT SELECT, INSERT, UPDATE, DELETE ON public.node_comments TO authenticated;

DROP POLICY IF EXISTS node_comments_collaborator_read ON public.node_comments;
CREATE POLICY node_comments_collaborator_read ON public.node_comments
    FOR SELECT TO authenticated
    USING (private.is_map_comment_collaborator(map_id));

DROP POLICY IF EXISTS node_comments_collaborator_insert ON public.node_comments;
CREATE POLICY node_comments_collaborator_insert ON public.node_comments
    FOR INSERT TO authenticated
    WITH CHECK (
        private.is_map_comment_collaborator(map_id)
        AND user_id = (SELECT auth.uid())
    );

DROP POLICY IF EXISTS node_comments_collaborator_update ON public.node_comments;
CREATE POLICY node_comments_collaborator_update ON public.node_comments
    FOR UPDATE TO authenticated
    USING (
        private.is_map_comment_collaborator(map_id)
        AND user_id = (SELECT auth.uid())
    )
    WITH CHECK (
        private.is_map_comment_collaborator(map_id)
        AND user_id = (SELECT auth.uid())
    );

DROP POLICY IF EXISTS node_comments_collaborator_delete ON public.node_comments;
CREATE POLICY node_comments_collaborator_delete ON public.node_comments
    FOR DELETE TO authenticated
    USING (
        private.is_map_comment_collaborator(map_id)
        AND user_id = (SELECT auth.uid())
    );
COMMIT;
