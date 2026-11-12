-- Migration: Create memories table for S3-BE6 Memory API
CREATE TABLE IF NOT EXISTS public.memories (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    map_id UUID REFERENCES public.mind_maps(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    category VARCHAR(50) NOT NULL DEFAULT 'general',
    scope VARCHAR(20) NOT NULL DEFAULT 'global' CHECK (scope IN ('global', 'map')),
    is_active BOOLEAN NOT NULL DEFAULT true,
    content_hash VARCHAR(64) NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_map_scope CHECK (
        (scope = 'global' AND map_id IS NULL) OR
        (scope = 'map' AND map_id IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_memories_user_id ON public.memories(user_id);
CREATE INDEX IF NOT EXISTS idx_memories_map_id ON public.memories(map_id);
CREATE INDEX IF NOT EXISTS idx_memories_scope ON public.memories(scope);
CREATE INDEX IF NOT EXISTS idx_memories_user_hash ON public.memories(user_id, content_hash);
CREATE INDEX IF NOT EXISTS idx_memories_active ON public.memories(is_active);

ALTER TABLE public.memories ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE tablename = 'memories' AND policyname = 'Users can view their own memories'
    ) THEN
        CREATE POLICY "Users can view their own memories" ON public.memories FOR SELECT USING (auth.uid() = user_id);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE tablename = 'memories' AND policyname = 'Users can insert their own memories'
    ) THEN
        CREATE POLICY "Users can insert their own memories" ON public.memories FOR INSERT WITH CHECK (auth.uid() = user_id);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE tablename = 'memories' AND policyname = 'Users can update their own memories'
    ) THEN
        CREATE POLICY "Users can update their own memories" ON public.memories FOR UPDATE USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE tablename = 'memories' AND policyname = 'Users can delete their own memories'
    ) THEN
        CREATE POLICY "Users can delete their own memories" ON public.memories FOR DELETE USING (auth.uid() = user_id);
    END IF;
END $$;
