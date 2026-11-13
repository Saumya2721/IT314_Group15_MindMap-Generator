-- Add summary column to chat_threads for short-term memory assembly and rolling context (US-15 / S2-BE4)
ALTER TABLE public.chat_threads
ADD COLUMN IF NOT EXISTS summary TEXT DEFAULT NULL;

COMMENT ON COLUMN public.chat_threads.summary IS 'Rolling summary of prior conversation turns for short-term LLM context assembly';
