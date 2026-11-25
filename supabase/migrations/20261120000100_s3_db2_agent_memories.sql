-- S3-DB2 / Issue #163: extend the existing Sprint 2 memory table without
-- breaking legacy memory_type/is_active records or backend service writes.
BEGIN;

-- S2-DB3 already created agent_memories with user/map cascade foreign keys.
-- Keep legacy fields for backward compatibility; add the Sprint 3 fields.
ALTER TABLE public.agent_memories
    ADD COLUMN kind text NOT NULL DEFAULT 'fact',
    ADD COLUMN importance double precision NOT NULL DEFAULT 1.0,
    ADD COLUMN last_accessed_at timestamptz NOT NULL DEFAULT now(),
    ADD COLUMN disabled_at timestamptz,
    ADD CONSTRAINT chk_agent_memories_kind
        CHECK (kind IN ('preference', 'decision', 'fact')),
    ADD CONSTRAINT chk_agent_memories_importance
        CHECK (importance >= 0.0 AND importance <= 1.0);

-- A direct auth.users FK is needed by S3-DB2; the existing profiles FK is
-- retained for backward compatibility with the original Sprint 2 schema.
ALTER TABLE public.agent_memories
    ADD CONSTRAINT agent_memories_user_auth_fkey
        FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE;

-- Preserve the meaning of any existing Sprint 2 records where possible.
UPDATE public.agent_memories
SET kind = CASE
    WHEN memory_type IN ('preference', 'decision', 'fact') THEN memory_type
    ELSE 'fact'
END;

-- Old writers still supply memory_type; new writers can supply kind. These
-- harmless defaults allow either style, while preserving the older schema.
ALTER TABLE public.agent_memories
    ALTER COLUMN memory_type SET DEFAULT 'fact';

CREATE INDEX idx_agent_memories_user_map
    ON public.agent_memories (user_id, map_id);
CREATE INDEX idx_agent_memories_user_kind_disabled
    ON public.agent_memories (user_id, kind, disabled_at);

-- Existing SELECT policy checks user and map ownership. S3-DB1 already
-- installs a RESTRICTIVE admin-no-SELECT policy, which must remain in place.
-- Allow updates to user-owned memory content/attributes but not user_id/map_id,
-- and allow deletion of only user-owned memories. Admin accounts are excluded.
CREATE POLICY agent_memories_update_own ON public.agent_memories
    FOR UPDATE TO authenticated
    USING (
        user_id = (SELECT auth.uid())
        AND (map_id IS NULL OR private.is_map_owner(map_id))
        AND NOT (SELECT private.is_admin())
    )
    WITH CHECK (
        user_id = (SELECT auth.uid())
        AND (map_id IS NULL OR private.is_map_owner(map_id))
        AND NOT (SELECT private.is_admin())
    );

CREATE POLICY agent_memories_delete_own ON public.agent_memories
    FOR DELETE TO authenticated
    USING (
        user_id = (SELECT auth.uid())
        AND (map_id IS NULL OR private.is_map_owner(map_id))
        AND NOT (SELECT private.is_admin())
    );

-- Only mutable non-ownership fields are writeable by authenticated clients.
-- INSERT remains backend/service-role only, as in Sprint 2.
GRANT UPDATE (memory_type, kind, content, is_active, importance,
              last_accessed_at, disabled_at)
    ON public.agent_memories TO authenticated;
GRANT DELETE ON public.agent_memories TO authenticated;

-- Security-invoker view: the caller's table privileges and RLS are honored.
-- One JSON bundle per owner, including disabled/global/map-scoped memories.
-- Admins receive no records, including from their own user account.
CREATE VIEW public.agent_memories_gdpr_export
WITH (security_invoker = true, security_barrier = true)
AS
SELECT m.user_id,
       jsonb_agg(
           jsonb_build_object(
               'id', m.id,
               'user_id', m.user_id,
               'map_id', m.map_id,
               'kind', m.kind,
               'content', m.content,
               'importance', m.importance,
               'last_accessed_at', m.last_accessed_at,
               'disabled_at', m.disabled_at,
               'created_at', m.created_at
           ) ORDER BY m.created_at, m.id
       ) AS memories
FROM public.agent_memories AS m
WHERE m.user_id = (SELECT auth.uid())
  AND NOT (SELECT private.is_admin())
GROUP BY m.user_id;

REVOKE ALL ON public.agent_memories_gdpr_export FROM PUBLIC, anon;
GRANT SELECT ON public.agent_memories_gdpr_export TO authenticated, service_role;

COMMIT;
