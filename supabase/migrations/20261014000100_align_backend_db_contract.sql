-- Align the existing Supabase schema with the current asyncpg backend contract.
-- Forward-only migration: preserve existing data and legacy pgvector objects.

BEGIN;

-- The backend stores a hash of the internal callback token with each job.
ALTER TABLE public.generation_jobs
    ADD COLUMN IF NOT EXISTS callback_token_hash VARCHAR(64);

-- The current retrieval implementation stores vectors in Pinecone and only
-- keeps the Pinecone vector ID plus source metadata in public.embeddings.
ALTER TABLE public.embeddings
    ADD COLUMN IF NOT EXISTS pinecone_vector_id VARCHAR(100);

-- Keep the old pgvector column for backwards compatibility, but allow the
-- backend to insert Pinecone metadata without also supplying a local vector.
ALTER TABLE public.embeddings
    ALTER COLUMN embedding DROP NOT NULL;

COMMIT;
