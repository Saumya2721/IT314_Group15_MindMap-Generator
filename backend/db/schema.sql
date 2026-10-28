-- =====================================================================
-- Mind Map Generator — Database Schema (v3, Supabase Auth)
-- PostgreSQL 15+ on Supabase (vectors live in Pinecone, not pgvector)
-- Group 15, IT314 Software Engineering
--
-- What changed from v2 (full list in the migration summary document):
--   * Authentication is delegated to Supabase Auth (auth.users).
--     The old "users" table becomes public.profiles, whose id IS the
--     auth.users.id (same UUID, 1:1, ON DELETE CASCADE). All password,
--     OAuth, email-verification and session columns are gone.
--   * The "sessions" table is removed: Supabase Auth issues and
--     refreshes JWTs and stores refresh tokens itself.
--   * mind_maps.user_id and chat_threads.user_id now reference
--     public.profiles(id).
--   * Row Level Security is enabled on every application table.
--     Ownership has ONE source of truth: mind_maps.user_id. Every
--     child table proves ownership by walking back to its mind_map.
--   * Fixed a v2 bug: deleting a node that a 'regenerate_node' job
--     targeted (or deleting a whole map / user account that had such
--     a job) failed with a CHECK violation (see section 6).
--
-- Unchanged from v2: every other table, column, CHECK constraint,
-- index and all same-map integrity triggers.
--
-- Source of truth: supabase/migrations/ (apply with `supabase db push`;
-- see supabase/README.md). This file is the SAME final schema as one
-- script, kept in sync with those migrations, for a fresh project set up
-- by hand. Differences from a migrated database: none, except that a
-- migrated database also keeps the deprecated, always-NULL pgvector column
-- public.embeddings.embedding (vectors live in Pinecone).
--
-- Run this whole file ONCE in the Supabase SQL Editor of a FRESH project
-- (it runs as the "postgres" role). If you do, mark the migrations as
-- applied before any later `supabase db push`:
--   supabase migration repair --status applied 20260930000000 20260930000100 \
--     20261014000000 20261014000100 20261015000000
-- =====================================================================

-- On Supabase, extensions live in the "extensions" schema.
-- gen_random_uuid() is built into Postgres core since v13.
-- Note: Dense vector embeddings and RAG retrieval are canonically hosted in Pinecone (namespaced per map_id).

-- Schema for internal helper functions used by RLS policies. It is NOT
-- exposed through the Supabase Data API (only "public" is), so these
-- functions cannot be called directly as REST/RPC endpoints.
CREATE SCHEMA IF NOT EXISTS private;

-- =====================================================================
-- 0. SHARED TRIGGER: keep updated_at current
-- =====================================================================
CREATE OR REPLACE FUNCTION public.set_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = ''
AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

-- =====================================================================
-- 1. PROFILES  (replaces v2 "users")
-- Application-specific data about a user. Identity, email, password,
-- OAuth identities and sessions all live in auth.users (Supabase Auth).
-- profiles.id is the SAME UUID as auth.users.id — never a new UUID.
-- =====================================================================
CREATE TABLE public.profiles (
    id          UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    name        VARCHAR(255),          -- display name shown in the app
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TRIGGER trg_profiles_updated_at
    BEFORE UPDATE ON public.profiles
    FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

-- Auto-create a profile whenever Supabase Auth creates a user (email
-- sign-up OR first Google sign-in). SECURITY DEFINER because the
-- insert into auth.users is performed by Supabase's internal auth role,
-- which has no privileges on public.profiles.
-- Keep this function trivial: if it raises, the sign-up itself fails.
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
BEGIN
    INSERT INTO public.profiles (id, name)
    VALUES (
        NEW.id,
        -- Google puts the name in full_name/name; for email sign-up the
        -- frontend can pass it via signUp({ options: { data: { name } } }).
        LEFT(NULLIF(BTRIM(COALESCE(
            NEW.raw_user_meta_data ->> 'full_name',
            NEW.raw_user_meta_data ->> 'name'
        )), ''), 255)
    )
    ON CONFLICT (id) DO NOTHING;
    RETURN NEW;
END;
$$;

REVOKE ALL ON FUNCTION public.handle_new_user() FROM PUBLIC, anon, authenticated;

CREATE TRIGGER on_auth_user_created
    AFTER INSERT ON auth.users
    FOR EACH ROW EXECUTE FUNCTION public.handle_new_user();

-- Backfill: harmless on a fresh project; creates profiles for any
-- auth users that already existed before this script was run.
INSERT INTO public.profiles (id, name)
SELECT u.id,
       LEFT(NULLIF(BTRIM(COALESCE(
           u.raw_user_meta_data ->> 'full_name',
           u.raw_user_meta_data ->> 'name'
       )), ''), 255)
FROM auth.users u
ON CONFLICT (id) DO NOTHING;

-- =====================================================================
-- 2. SESSIONS — REMOVED
-- Supabase Auth issues access tokens (JWTs), stores/rotates refresh
-- tokens (auth.refresh_tokens) and tracks sessions (auth.sessions).
-- =====================================================================

-- =====================================================================
-- 3. MIND_MAPS
-- =====================================================================
CREATE TABLE public.mind_maps (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- DEFAULT auth.uid(): a direct insert by a logged-in user is owned by
    -- that user automatically. A backend connection with no JWT gets NULL
    -- here, so it must pass user_id explicitly (NOT NULL fails otherwise).
    user_id     UUID NOT NULL DEFAULT auth.uid()
                REFERENCES public.profiles(id) ON DELETE CASCADE,
    title       VARCHAR(255) NOT NULL DEFAULT 'Untitled mind map',
    description TEXT,
    is_public   BOOLEAN NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_mind_maps_user_id_created ON public.mind_maps (user_id, created_at DESC);
-- GET /api/maps: WHERE user_id = $1 ORDER BY updated_at DESC
CREATE INDEX idx_mind_maps_user_id_updated ON public.mind_maps (user_id, updated_at DESC);

CREATE TRIGGER trg_mind_maps_updated_at
    BEFORE UPDATE ON public.mind_maps
    FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

-- Ownership is immutable: every RLS policy derives access from
-- mind_maps.user_id, so a map must never silently change owner
-- (this also keeps chat_threads.user_id in sync, see section 4).
CREATE OR REPLACE FUNCTION public.prevent_mind_map_owner_change()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = ''
AS $$
BEGIN
    IF NEW.user_id IS DISTINCT FROM OLD.user_id THEN
        RAISE EXCEPTION 'mind_map %: owner (user_id) cannot be changed', OLD.id;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_mind_maps_owner_immutable
    BEFORE UPDATE OF user_id ON public.mind_maps
    FOR EACH ROW EXECUTE FUNCTION public.prevent_mind_map_owner_change();

-- =====================================================================
-- 4. CHAT_THREADS  (exactly one thread per map; thread_id == LangGraph
--    thread_id; regenerate requests reuse this same thread)
-- =====================================================================
CREATE TABLE public.chat_threads (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    map_id      UUID NOT NULL UNIQUE REFERENCES public.mind_maps(id) ON DELETE CASCADE,
    user_id     UUID NOT NULL REFERENCES public.profiles(id) ON DELETE CASCADE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_chat_threads_user_id ON public.chat_threads (user_id);

-- chat_threads.user_id duplicates mind_maps.user_id; make sure they can
-- never disagree (otherwise a thread could "belong" to someone other
-- than the map's owner).
CREATE OR REPLACE FUNCTION public.check_chat_thread_owner_matches_map()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = ''
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM public.mind_maps WHERE id = NEW.map_id AND user_id = NEW.user_id
    ) THEN
        RAISE EXCEPTION 'chat_thread %: user_id % is not the owner of map %',
            NEW.id, NEW.user_id, NEW.map_id;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_chat_threads_owner_matches_map
    BEFORE INSERT OR UPDATE OF user_id, map_id ON public.chat_threads
    FOR EACH ROW EXECUTE FUNCTION public.check_chat_thread_owner_matches_map();

-- =====================================================================
-- 5. CHAT_MESSAGES  (human-readable transcript for the UI)
-- =====================================================================
CREATE TABLE public.chat_messages (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    thread_id   UUID NOT NULL REFERENCES public.chat_threads(id) ON DELETE CASCADE,
    role        VARCHAR(20) NOT NULL CHECK (role IN ('user', 'assistant')),
    content     TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_chat_messages_thread_id_created ON public.chat_messages (thread_id, created_at);

-- =====================================================================
-- 6. GENERATION_JOBS  (async task tracking only — no result payload here)
-- =====================================================================
CREATE TABLE public.generation_jobs (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    map_id                  UUID NOT NULL REFERENCES public.mind_maps(id) ON DELETE CASCADE,
    triggered_by_message_id UUID REFERENCES public.chat_messages(id) ON DELETE SET NULL,

    job_type          VARCHAR(20) NOT NULL DEFAULT 'generate'
                      CHECK (job_type IN ('generate', 'regenerate_full', 'regenerate_node')),
    target_node_id    UUID,   -- FK to nodes(id) added below, after nodes exists

    -- TEXT, not VARCHAR: internal.report_fail() binds one parameter both as
    -- the new status and in "CASE WHEN $2 = 'rejected'"; with a VARCHAR
    -- column asyncpg's statement fails with 42P08 (inconsistent types).
    status            TEXT NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('pending', 'running', 'completed', 'failed', 'rejected')),
    progress_percent  INT NOT NULL DEFAULT 0 CHECK (progress_percent BETWEEN 0 AND 100),
    error_message     TEXT,

    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at        TIMESTAMPTZ,
    completed_at      TIMESTAMPTZ,
    callback_token_hash VARCHAR(64),

    -- CHANGED from v2. v2 required target_node_id IS NOT NULL for every
    -- 'regenerate_node' job, but the FK below is ON DELETE SET NULL, so
    -- deleting that node later (FR-13), deleting the map, or deleting the
    -- user's account violated this CHECK and the delete failed.
    -- Now: only regenerate_node jobs may have a target, and "a target is
    -- required when the job is CREATED" is enforced by the INSERT branch
    -- of trg_generation_jobs_target_node_same_map (section 11).
    CONSTRAINT chk_target_node_matches_job_type CHECK (
        job_type = 'regenerate_node' OR target_node_id IS NULL
    ),

    -- Job lifecycle: what each status implies about the other columns.
    --   pending   -> nothing has happened yet
    --   running   -> started, not finished, 0-99%, no error
    --   completed -> started, finished, 100%, no error
    --   failed    -> started, then broke partway through
    --   rejected  -> never started at all (rejected at intake)
    CONSTRAINT chk_generation_job_lifecycle CHECK (
        (status = 'pending'
            AND started_at IS NULL AND completed_at IS NULL
            AND progress_percent = 0 AND error_message IS NULL)
        OR (status = 'running'
            AND started_at IS NOT NULL AND completed_at IS NULL
            AND progress_percent BETWEEN 0 AND 99 AND error_message IS NULL)
        OR (status = 'completed'
            AND started_at IS NOT NULL AND completed_at IS NOT NULL
            AND progress_percent = 100 AND error_message IS NULL)
        OR (status = 'failed'
            AND started_at IS NOT NULL AND completed_at IS NOT NULL
            AND progress_percent BETWEEN 0 AND 99 AND error_message IS NOT NULL)
        OR (status = 'rejected'
            AND started_at IS NULL AND completed_at IS NOT NULL
            AND progress_percent = 0 AND error_message IS NOT NULL)
    ),

    CONSTRAINT chk_generation_timestamps CHECK (
        (started_at IS NULL OR started_at >= created_at) AND
        (completed_at IS NULL OR completed_at >= created_at) AND
        (completed_at IS NULL OR started_at IS NULL OR completed_at >= started_at)
    )
);

-- (map_id, created_at DESC) serves latest-job polling, has_active_job()
-- and the ON DELETE CASCADE from mind_maps.
CREATE INDEX idx_generation_jobs_map_id_created  ON public.generation_jobs (map_id, created_at DESC);
CREATE INDEX idx_generation_jobs_status_created  ON public.generation_jobs (status, created_at);
CREATE INDEX idx_generation_jobs_target_node_id  ON public.generation_jobs (target_node_id);
CREATE INDEX idx_generation_jobs_triggered_by_message_id
    ON public.generation_jobs (triggered_by_message_id);

COMMENT ON COLUMN public.generation_jobs.callback_token_hash IS
    'SHA-256 hex digest of the per-job worker callback bearer token. Backend-only; not readable by API users.';

-- At most one active full-map generation/regeneration per map at a time;
-- node-level regens may run concurrently for different nodes.
CREATE UNIQUE INDEX uq_generation_jobs_active_full_map
    ON public.generation_jobs (map_id)
    WHERE status IN ('pending', 'running') AND job_type IN ('generate', 'regenerate_full');

-- =====================================================================
-- 7. SEED_ASSETS
-- Binary files live in Supabase Storage (private bucket "seed-assets",
-- section 14); storage_key is the object path inside that bucket.
-- content_text holds extracted/OCR text, or the raw text for text assets.
-- =====================================================================
CREATE TABLE public.seed_assets (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    generation_job_id  UUID NOT NULL REFERENCES public.generation_jobs(id) ON DELETE CASCADE,
    asset_type         VARCHAR(20) NOT NULL CHECK (asset_type IN ('pdf', 'image', 'text')),
    file_name          VARCHAR(255),
    storage_key        TEXT,
    mime_type          VARCHAR(100),
    content_text       TEXT,
    file_size_bytes    BIGINT,
    checksum_sha256    VARCHAR(64),
    uploaded_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT chk_seed_asset_size CHECK (file_size_bytes IS NULL OR file_size_bytes >= 0),
    CONSTRAINT chk_seed_asset_storage CHECK (
        (asset_type = 'text' AND storage_key IS NULL)
        OR (asset_type IN ('pdf', 'image') AND storage_key IS NOT NULL AND file_name IS NOT NULL)
    ),
    CONSTRAINT chk_seed_asset_checksum CHECK (
        checksum_sha256 IS NULL OR checksum_sha256 ~ '^[0-9A-Fa-f]{64}$'
    )
);

CREATE INDEX idx_seed_assets_job_id ON public.seed_assets (generation_job_id);

-- =====================================================================
-- 8. NODES  (canonical mind-map structure)
-- =====================================================================
CREATE TABLE public.nodes (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    map_id            UUID NOT NULL REFERENCES public.mind_maps(id) ON DELETE CASCADE,
    label             VARCHAR(255) NOT NULL,
    description       TEXT,
    pos_x             DOUBLE PRECISION NOT NULL DEFAULT 0,
    pos_y             DOUBLE PRECISION NOT NULL DEFAULT 0,
    background_color  VARCHAR(7),
    text_color        VARCHAR(7),
    width             DOUBLE PRECISION,   -- NULL = frontend auto-sizes from content
    height            DOUBLE PRECISION,   -- non-NULL once a user manually resizes it
    node_type         VARCHAR(50) NOT NULL DEFAULT 'default',
    source_job_id     UUID REFERENCES public.generation_jobs(id) ON DELETE SET NULL,
    node_data         JSONB,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT chk_nodes_dimensions CHECK (
        (width IS NULL OR width > 0) AND (height IS NULL OR height > 0)
    ),
    CONSTRAINT chk_nodes_background_color CHECK (
        background_color IS NULL OR background_color ~ '^#[0-9A-Fa-f]{6}$'
    ),
    CONSTRAINT chk_nodes_text_color CHECK (
        text_color IS NULL OR text_color ~ '^#[0-9A-Fa-f]{6}$'
    )
);

CREATE INDEX idx_nodes_map_id        ON public.nodes (map_id);
CREATE INDEX idx_nodes_source_job_id ON public.nodes (source_job_id);

CREATE TRIGGER trg_nodes_updated_at
    BEFORE UPDATE ON public.nodes
    FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

-- Deferred single-column FK: generation_jobs.target_node_id -> nodes.id.
-- Added here, after nodes exists, to resolve the circular reference with
-- nodes.source_job_id -> generation_jobs.id above.
ALTER TABLE public.generation_jobs
    ADD CONSTRAINT fk_generation_jobs_target_node
    FOREIGN KEY (target_node_id) REFERENCES public.nodes(id) ON DELETE SET NULL
    DEFERRABLE INITIALLY DEFERRED;

-- =====================================================================
-- 9. EDGES  (relationships between nodes)
-- =====================================================================
CREATE TABLE public.edges (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    map_id          UUID NOT NULL REFERENCES public.mind_maps(id) ON DELETE CASCADE,
    source_node_id  UUID NOT NULL REFERENCES public.nodes(id) ON DELETE CASCADE,
    target_node_id  UUID NOT NULL REFERENCES public.nodes(id) ON DELETE CASCADE,
    label           VARCHAR(100),          -- e.g. 'causes', 'contains', 'is-a'
    edge_data       JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT chk_edges_no_self_loop CHECK (source_node_id <> target_node_id)
);

CREATE INDEX idx_edges_map_id         ON public.edges (map_id);
CREATE INDEX idx_edges_source_node_id ON public.edges (source_node_id);
CREATE INDEX idx_edges_target_node_id ON public.edges (target_node_id);

-- Treat NULL labels as equal for uniqueness, so two identical unlabeled
-- edges between the same nodes can't both be inserted (PG15+).
CREATE UNIQUE INDEX uq_edges_source_target_label
    ON public.edges (source_node_id, target_node_id, label) NULLS NOT DISTINCT;

-- =====================================================================
-- 10. SEED CHUNKS / RETRIEVAL METADATA
-- NOTE: In the decoupled worker architecture, dense vector embeddings and
-- similarity search are owned by Pinecone (namespaced per map_id).
-- The embeddings table below records text chunks and Pinecone vector IDs
-- for relational provenance and audits without requiring pgvector.
-- =====================================================================
CREATE TABLE public.embeddings (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    generation_job_id  UUID NOT NULL REFERENCES public.generation_jobs(id) ON DELETE CASCADE,
    map_id             UUID NOT NULL REFERENCES public.mind_maps(id) ON DELETE CASCADE,
    chunk_text         TEXT NOT NULL,
    pinecone_vector_id VARCHAR(100),
    embedding_model    VARCHAR(100) NOT NULL,
    source             VARCHAR(20) NOT NULL CHECK (source IN ('uploaded_seed', 'web_search', 'arxiv')),
    source_title       TEXT,
    source_url         TEXT,
    retrieved_at       TIMESTAMPTZ,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT chk_embeddings_source_url CHECK (
        (source IN ('web_search', 'arxiv') AND source_url IS NOT NULL)
        OR (source = 'uploaded_seed')
    )
);

CREATE INDEX idx_embeddings_job_id ON public.embeddings (generation_job_id);
CREATE INDEX idx_embeddings_map_id ON public.embeddings (map_id);
CREATE INDEX idx_embeddings_pinecone_vector_id ON public.embeddings (pinecone_vector_id);

-- =====================================================================
-- 11. SAME-MAP INTEGRITY TRIGGERS
--
-- Every FK above only guarantees "the referenced row exists somewhere."
-- These triggers add the extra guarantee "...and it belongs to the same
-- mind_map as this row." Under Supabase this is ALSO a security control:
-- FK checks ignore RLS, so without these triggers a user could point a
-- row in their own map at a node/job UUID from somebody else's map.
-- =====================================================================

CREATE OR REPLACE FUNCTION public.check_node_source_job_same_map()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = ''
AS $$
BEGIN
    IF NEW.source_job_id IS NOT NULL THEN
        IF NOT EXISTS (
            SELECT 1 FROM public.generation_jobs WHERE id = NEW.source_job_id AND map_id = NEW.map_id
        ) THEN
            RAISE EXCEPTION 'node %: source_job_id % does not belong to map %',
                NEW.id, NEW.source_job_id, NEW.map_id;
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_nodes_source_job_same_map
    BEFORE INSERT OR UPDATE OF source_job_id, map_id ON public.nodes
    FOR EACH ROW EXECUTE FUNCTION public.check_node_source_job_same_map();


CREATE OR REPLACE FUNCTION public.check_job_target_node_same_map()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = ''
AS $$
BEGIN
    -- A node-level regeneration must name its target when it is created.
    -- (It may become NULL later if that node is deleted — see section 6.)
    IF TG_OP = 'INSERT' AND NEW.job_type = 'regenerate_node' AND NEW.target_node_id IS NULL THEN
        RAISE EXCEPTION 'generation_job %: regenerate_node jobs require target_node_id', NEW.id;
    END IF;
    IF NEW.target_node_id IS NOT NULL THEN
        IF NOT EXISTS (
            SELECT 1 FROM public.nodes WHERE id = NEW.target_node_id AND map_id = NEW.map_id
        ) THEN
            RAISE EXCEPTION 'generation_job %: target_node_id % does not belong to map %',
                NEW.id, NEW.target_node_id, NEW.map_id;
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_generation_jobs_target_node_same_map
    BEFORE INSERT OR UPDATE OF target_node_id, map_id ON public.generation_jobs
    FOR EACH ROW EXECUTE FUNCTION public.check_job_target_node_same_map();


CREATE OR REPLACE FUNCTION public.check_edge_endpoints_same_map()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = ''
AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM public.nodes WHERE id = NEW.source_node_id AND map_id = NEW.map_id) THEN
        RAISE EXCEPTION 'edge %: source_node_id % does not belong to map %',
            NEW.id, NEW.source_node_id, NEW.map_id;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM public.nodes WHERE id = NEW.target_node_id AND map_id = NEW.map_id) THEN
        RAISE EXCEPTION 'edge %: target_node_id % does not belong to map %',
            NEW.id, NEW.target_node_id, NEW.map_id;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_edges_endpoints_same_map
    BEFORE INSERT OR UPDATE OF source_node_id, target_node_id, map_id ON public.edges
    FOR EACH ROW EXECUTE FUNCTION public.check_edge_endpoints_same_map();


CREATE OR REPLACE FUNCTION public.check_embedding_job_same_map()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = ''
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM public.generation_jobs WHERE id = NEW.generation_job_id AND map_id = NEW.map_id
    ) THEN
        RAISE EXCEPTION 'embedding %: generation_job_id % does not belong to map %',
            NEW.id, NEW.generation_job_id, NEW.map_id;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_embeddings_job_same_map
    BEFORE INSERT OR UPDATE OF generation_job_id, map_id ON public.embeddings
    FOR EACH ROW EXECUTE FUNCTION public.check_embedding_job_same_map();


-- Belt-and-suspenders: chat_threads has UNIQUE(map_id), so this bad
-- state is structurally almost unreachable already.
CREATE OR REPLACE FUNCTION public.check_job_trigger_message_same_map()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = ''
AS $$
BEGIN
    IF NEW.triggered_by_message_id IS NOT NULL THEN
        IF NOT EXISTS (
            SELECT 1
            FROM public.chat_messages m
            JOIN public.chat_threads t ON t.id = m.thread_id
            WHERE m.id = NEW.triggered_by_message_id AND t.map_id = NEW.map_id
        ) THEN
            RAISE EXCEPTION 'generation_job %: triggered_by_message_id % is not from a thread on map %',
                NEW.id, NEW.triggered_by_message_id, NEW.map_id;
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_generation_jobs_trigger_message_same_map
    BEFORE INSERT OR UPDATE OF triggered_by_message_id, map_id ON public.generation_jobs
    FOR EACH ROW EXECUTE FUNCTION public.check_job_trigger_message_same_map();

-- =====================================================================
-- 12. ROW LEVEL SECURITY
--
-- Who can reach these tables?
--   * anon / authenticated  -> the Supabase Data API (PostgREST/Realtime)
--     using the PUBLIC anon key + the user's JWT. RLS below applies.
--   * FastAPI backend        -> connects as "postgres" (table owner) or
--     uses the service_role key. Both BYPASS RLS, so the backend must
--     check ownership itself (see the backend notes).
--
-- Single source of truth for ownership: mind_maps.user_id = auth.uid().
-- Child tables prove ownership by walking back to their mind_map:
--   nodes, edges, embeddings, generation_jobs, chat_threads -> map_id
--   chat_messages -> chat_threads.map_id
--   seed_assets   -> generation_jobs.map_id
--
-- Write access through the Data API is deliberately narrow:
--   * users may edit their profile name, and create/edit/delete their
--     maps, nodes and edges (canvas editing, FR-11..13);
--   * chat, generation jobs, seed assets and embeddings are READ-ONLY for
--     users — only the FastAPI backend writes them. Otherwise a user
--     could forge assistant messages or "completed" jobs, or queue jobs
--     directly and bypass the backend's rate limiting (DR-01).
-- =====================================================================

-- Helper functions (in the non-exposed "private" schema).
-- SECURITY DEFINER lets them read mind_maps without re-triggering RLS
-- inside every policy; they only ever answer about the CALLER's own
-- ownership (auth.uid()) or about the map's public flag.
CREATE OR REPLACE FUNCTION private.is_map_owner(p_map_id UUID)
RETURNS BOOLEAN
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = ''
AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.mind_maps m
        WHERE m.id = p_map_id
          AND m.user_id = (SELECT auth.uid())
    );
$$;

CREATE OR REPLACE FUNCTION private.is_public_map(p_map_id UUID)
RETURNS BOOLEAN
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = ''
AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.mind_maps m
        WHERE m.id = p_map_id
          AND m.is_public
    );
$$;

REVOKE ALL ON SCHEMA private FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA private FROM PUBLIC;
GRANT USAGE ON SCHEMA private TO anon, authenticated;
GRANT EXECUTE ON FUNCTION private.is_map_owner(UUID)  TO authenticated;
GRANT EXECUTE ON FUNCTION private.is_public_map(UUID) TO anon, authenticated;

ALTER TABLE public.profiles        ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.mind_maps       ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.chat_threads    ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.chat_messages   ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.generation_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.seed_assets     ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.nodes           ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.edges           ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.embeddings      ENABLE ROW LEVEL SECURITY;

-- ---------- profiles: each user sees/edits only their own row ----------
CREATE POLICY profiles_select_own ON public.profiles
    FOR SELECT TO authenticated
    USING (id = (SELECT auth.uid()));

-- Normally the auth trigger creates the profile; this allows a
-- "create my profile if missing" fallback, but only for yourself.
CREATE POLICY profiles_insert_own ON public.profiles
    FOR INSERT TO authenticated
    WITH CHECK (id = (SELECT auth.uid()));

CREATE POLICY profiles_update_own ON public.profiles
    FOR UPDATE TO authenticated
    USING (id = (SELECT auth.uid()))
    WITH CHECK (id = (SELECT auth.uid()));
-- No DELETE policy: deleting the auth user (account deletion) cascades.

-- ---------- mind_maps: owner has full CRUD ----------
CREATE POLICY mind_maps_select_own ON public.mind_maps
    FOR SELECT TO authenticated
    USING (user_id = (SELECT auth.uid()));

-- Read-only access to maps the owner explicitly marked is_public
-- (default FALSE, so nothing is public by accident).
CREATE POLICY mind_maps_select_public ON public.mind_maps
    FOR SELECT TO anon, authenticated
    USING (is_public);

CREATE POLICY mind_maps_insert_own ON public.mind_maps
    FOR INSERT TO authenticated
    WITH CHECK (user_id = (SELECT auth.uid()));

CREATE POLICY mind_maps_update_own ON public.mind_maps
    FOR UPDATE TO authenticated
    USING (user_id = (SELECT auth.uid()))
    WITH CHECK (user_id = (SELECT auth.uid()));

CREATE POLICY mind_maps_delete_own ON public.mind_maps
    FOR DELETE TO authenticated
    USING (user_id = (SELECT auth.uid()));

-- ---------- nodes: CRUD if you own the parent map ----------
CREATE POLICY nodes_select_own ON public.nodes
    FOR SELECT TO authenticated
    USING (private.is_map_owner(map_id));

CREATE POLICY nodes_select_public ON public.nodes
    FOR SELECT TO anon, authenticated
    USING (private.is_public_map(map_id));

CREATE POLICY nodes_insert_own ON public.nodes
    FOR INSERT TO authenticated
    WITH CHECK (private.is_map_owner(map_id));

CREATE POLICY nodes_update_own ON public.nodes
    FOR UPDATE TO authenticated
    USING (private.is_map_owner(map_id))
    WITH CHECK (private.is_map_owner(map_id));

CREATE POLICY nodes_delete_own ON public.nodes
    FOR DELETE TO authenticated
    USING (private.is_map_owner(map_id));

-- ---------- edges: CRUD if you own the parent map ----------
-- (The same-map trigger additionally guarantees both endpoints are
-- nodes of that same map, so an edge can't reach into another map.)
CREATE POLICY edges_select_own ON public.edges
    FOR SELECT TO authenticated
    USING (private.is_map_owner(map_id));

CREATE POLICY edges_select_public ON public.edges
    FOR SELECT TO anon, authenticated
    USING (private.is_public_map(map_id));

CREATE POLICY edges_insert_own ON public.edges
    FOR INSERT TO authenticated
    WITH CHECK (private.is_map_owner(map_id));

CREATE POLICY edges_update_own ON public.edges
    FOR UPDATE TO authenticated
    USING (private.is_map_owner(map_id))
    WITH CHECK (private.is_map_owner(map_id));

CREATE POLICY edges_delete_own ON public.edges
    FOR DELETE TO authenticated
    USING (private.is_map_owner(map_id));

-- ---------- backend-written tables: owner may READ only ----------
-- Chat history, job progress, uploads and retrieved sources are private
-- even when the map is public (raw ideas stay private — Conflict #5).
CREATE POLICY chat_threads_select_own ON public.chat_threads
    FOR SELECT TO authenticated
    USING (private.is_map_owner(map_id));

CREATE POLICY chat_messages_select_own ON public.chat_messages
    FOR SELECT TO authenticated
    USING (EXISTS (
        SELECT 1 FROM public.chat_threads t
        WHERE t.id = chat_messages.thread_id
          AND private.is_map_owner(t.map_id)
    ));

-- Also lets the frontend subscribe to job progress via Supabase Realtime
-- (NFR-12), which honours RLS SELECT policies.
CREATE POLICY generation_jobs_select_own ON public.generation_jobs
    FOR SELECT TO authenticated
    USING (private.is_map_owner(map_id));

CREATE POLICY seed_assets_select_own ON public.seed_assets
    FOR SELECT TO authenticated
    USING (EXISTS (
        SELECT 1 FROM public.generation_jobs j
        WHERE j.id = seed_assets.generation_job_id
          AND private.is_map_owner(j.map_id)
    ));

CREATE POLICY embeddings_select_own ON public.embeddings
    FOR SELECT TO authenticated
    USING (private.is_map_owner(map_id));

-- =====================================================================
-- 13. TABLE PRIVILEGES (defence in depth on top of RLS)
-- Supabase grants ALL on new public tables to anon and authenticated by
-- default. RLS already filters rows; these grants additionally limit
-- which operations/columns the Data API can touch at all.
-- =====================================================================
REVOKE ALL ON
    public.profiles, public.mind_maps, public.chat_threads, public.chat_messages,
    public.generation_jobs, public.seed_assets, public.nodes, public.edges,
    public.embeddings
FROM anon, authenticated;

-- anon: may only read public maps (rows limited by the *_select_public policies)
GRANT SELECT ON public.mind_maps, public.nodes, public.edges TO anon;

-- authenticated
GRANT SELECT, INSERT ON public.profiles TO authenticated;
GRANT UPDATE (name) ON public.profiles TO authenticated;

GRANT SELECT, INSERT, DELETE ON public.mind_maps TO authenticated;
GRANT UPDATE (title, description, is_public) ON public.mind_maps TO authenticated;

GRANT SELECT, INSERT, DELETE ON public.nodes TO authenticated;
GRANT UPDATE (label, description, pos_x, pos_y, background_color, text_color,
              width, height, node_type, node_data)
    ON public.nodes TO authenticated;

GRANT SELECT, INSERT, DELETE ON public.edges TO authenticated;
GRANT UPDATE (source_node_id, target_node_id, label, edge_data)
    ON public.edges TO authenticated;

GRANT SELECT ON public.chat_threads, public.chat_messages,
                public.seed_assets, public.embeddings
    TO authenticated;

-- Jobs: owners may read status/progress, but never callback_token_hash
-- (the secret the worker callbacks are authenticated with).
GRANT SELECT (id, map_id, triggered_by_message_id, job_type, target_node_id, status,
              progress_percent, error_message, created_at, started_at, completed_at)
    ON public.generation_jobs TO authenticated;

-- service_role (server-side only, bypasses RLS) keeps full access.
GRANT ALL ON
    public.profiles, public.mind_maps, public.chat_threads, public.chat_messages,
    public.generation_jobs, public.seed_assets, public.nodes, public.edges,
    public.embeddings
TO service_role;

-- =====================================================================
-- 14. SUPABASE STORAGE for seed uploads (images / PDFs)
-- Private bucket. Object path convention (set by the backend):
--     <user_id>/<map_id>/<generation_job_id>/<file_name>
-- and seed_assets.storage_key stores that path.
-- Uploads are done by the FastAPI backend (it must parse the file anyway,
-- DR-02); users may only READ files inside their own <user_id>/ folder.
-- =====================================================================
-- 25 MB and the MIME types match backend/app/uploads/service.py
-- (MAX_FILE_BYTES, sniff_mime()).
INSERT INTO storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
VALUES ('seed-assets', 'seed-assets', FALSE, 26214400,
        ARRAY['application/pdf', 'image/png', 'image/jpeg', 'image/webp'])
ON CONFLICT (id) DO UPDATE
    SET public             = FALSE,
        file_size_limit    = EXCLUDED.file_size_limit,
        allowed_mime_types = EXCLUDED.allowed_mime_types;

CREATE POLICY seed_assets_storage_select_own ON storage.objects
    FOR SELECT TO authenticated
    USING (
        bucket_id = 'seed-assets'
        AND (storage.foldername(name))[1] = (SELECT auth.uid())::text
    );

-- =====================================================================
-- 15. LANGGRAPH CHECKPOINTER SCHEMA
-- LangGraph's Postgres checkpointer creates its own tables on first run
-- (checkpoints, checkpoint_writes, checkpoint_blobs, ...). If created in
-- "public" they would be exposed through the Data API WITHOUT RLS.
-- Create them in this private schema instead by connecting the
-- checkpointer with search_path=langgraph (see backend notes).
-- =====================================================================
CREATE SCHEMA IF NOT EXISTS langgraph;
REVOKE ALL ON SCHEMA langgraph FROM PUBLIC;
REVOKE ALL ON SCHEMA langgraph FROM anon, authenticated;

-- Optional — live job progress in the UI via Supabase Realtime (NFR-12):
-- ALTER PUBLICATION supabase_realtime ADD TABLE public.generation_jobs;

-- =====================================================================
-- 16. DESIGN NOTES
-- =====================================================================
-- 1. Identity chain: auth.users.id = profiles.id = mind_maps.user_id.
--    One UUID per person, owned by Supabase Auth. Deleting the auth user
--    cascades: profile -> mind_maps -> threads/messages/jobs/assets/
--    nodes/edges/embeddings.
-- 2. No passwords, OAuth ids, refresh tokens or sessions are stored in
--    application tables — Supabase Auth owns all of that.
-- 3. Every PK/FK is single-column. "Same mind_map" integrity is enforced
--    by the triggers in section 11 (which now also block cross-user
--    references). chat_threads.user_id is the only denormalized owner
--    column and is kept equal to mind_maps.user_id by a trigger.
-- 4. chat_threads is one-to-one with mind_maps (UNIQUE(map_id)).
-- 5. generation_jobs.status distinguishes 'rejected' (never started)
--    from 'failed' (started, then broke).
-- 6. Collaboration/sharing (FR-20..25) and admin roles (FR-26/27) are
--    intentionally not modeled yet — Sprint 3. When added, extend the
--    private.is_map_owner() check (e.g. a map_members table) rather
--    than editing every policy.
-- =====================================================================
-- End of schema
-- =====================================================================
