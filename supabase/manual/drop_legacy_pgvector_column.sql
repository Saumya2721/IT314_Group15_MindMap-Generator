-- =====================================================================
-- OPTIONAL and DESTRUCTIVE. NOT a migration: the Supabase CLI never runs
-- files in this folder. Run it by hand only when you choose to.
--
-- What it does: drops the deprecated pgvector column
-- public.embeddings.embedding (and its HNSW index, if still present).
-- Vectors now live in Pinecone; the current backend never reads or
-- writes this column.
--
-- Safety: it refuses to run if any row still has a non-NULL vector,
-- unless you explicitly opt in for this session first with
--     set mindmap.allow_vector_drop = 'yes';
-- SQL cannot move old pgvector vectors into Pinecone. If you still need
-- them, re-embed the chunk_text into Pinecone BEFORE running this.
--
-- How to run (pick one):
--   * Supabase Dashboard -> SQL Editor -> paste this file -> Run
--   * psql "$DATABASE_URL" -f supabase/manual/drop_legacy_pgvector_column.sql
--
-- Run it on a fresh database right after `supabase db reset` / `db push`
-- if you want the exact shape of backend/db/schema.sql (no vector column).
-- The "vector" extension itself is left installed on purpose; remove it
-- from Dashboard -> Database -> Extensions only if nothing else uses it.
-- =====================================================================

begin;

do $$
declare
    remaining bigint;
begin
    if not exists (
        select 1 from information_schema.columns
        where table_schema = 'public' and table_name = 'embeddings' and column_name = 'embedding'
    ) then
        raise notice 'public.embeddings.embedding does not exist; nothing to do.';
        return;
    end if;

    execute 'select count(*) from public.embeddings where embedding is not null' into remaining;

    if remaining > 0 and coalesce(current_setting('mindmap.allow_vector_drop', true), '') <> 'yes' then
        raise exception
            'Refusing to drop public.embeddings.embedding: % row(s) still hold a vector. '
            'Re-embed them into Pinecone first, or run "set mindmap.allow_vector_drop = ''yes'';" '
            'in this session to drop them anyway.', remaining;
    end if;

    drop index if exists public.idx_embeddings_vector_hnsw;
    alter table public.embeddings drop column embedding;
    raise notice 'Dropped public.embeddings.embedding (% non-NULL vectors discarded).', remaining;
end;
$$;

commit;
