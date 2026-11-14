-- S2-DB3 / Issue #77: chat metadata, summary cursor and agent memories.
-- Existing chat_threads.summary is provided by 20261111000000_add_chat_threads_summary.sql.
BEGIN;

ALTER TABLE public.chat_messages
    ADD COLUMN IF NOT EXISTS metadata jsonb NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE public.chat_threads
    ADD COLUMN IF NOT EXISTS summary_through_message_id uuid
    REFERENCES public.chat_messages(id) ON DELETE SET NULL;

CREATE TABLE IF NOT EXISTS public.agent_memories (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    map_id uuid REFERENCES public.mind_maps(id) ON DELETE CASCADE,
    memory_type text NOT NULL,
    content text NOT NULL,
    is_active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_agent_memories_user_active
    ON public.agent_memories (user_id, is_active, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_memories_map
    ON public.agent_memories (map_id) WHERE map_id IS NOT NULL;

ALTER TABLE public.agent_memories ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS agent_memories_select_own ON public.agent_memories;
CREATE POLICY agent_memories_select_own ON public.agent_memories
    FOR SELECT TO authenticated
    USING (
        user_id = (select auth.uid())
        AND (map_id IS NULL OR private.is_map_owner(map_id))
    );

-- Memory records are created/updated by the backend, not directly by clients.
REVOKE ALL ON public.agent_memories FROM anon, authenticated;
GRANT SELECT ON public.agent_memories TO authenticated;
GRANT ALL ON public.agent_memories TO service_role;
COMMIT;
