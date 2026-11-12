-- Migration: Add proposed_action and is_confirmed to chat_messages for Sprint 2 (S2-BE3)
ALTER TABLE public.chat_messages
    ADD COLUMN IF NOT EXISTS proposed_action jsonb DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS is_confirmed boolean DEFAULT false;

CREATE INDEX IF NOT EXISTS idx_chat_messages_proposed_action
    ON public.chat_messages (thread_id)
    WHERE proposed_action IS NOT NULL;
