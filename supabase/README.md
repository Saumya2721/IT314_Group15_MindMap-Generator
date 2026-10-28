# Supabase — Mind Map Generator (Group 15, IT314)

Database, Auth and Storage setup for the FastAPI backend (`backend/`) and the
LLM worker (`llm/`). The backend is the only component that talks to the
database. The LLM worker has no database access: it gets jobs from Redis,
downloads seed files through signed Storage URLs, stores vectors in Pinecone,
and reports results to the backend over authenticated HTTP callbacks.

```
supabase/
├── config.toml          Local Supabase CLI config (no secrets; env() references only)
├── migrations/          Applied in filename order by `supabase db reset` / `supabase db push`
│   ├── 20260930000000_init_schema.sql                 v3 schema (users, maps, chat, jobs, seeds, nodes, edges, embeddings, RLS)
│   ├── 20260930000100_storage_seed_assets.sql         private "seed-assets" bucket
│   ├── 20261014000000_upgrade_profiles_rls.sql        users -> profiles, narrow RLS + column grants
│   ├── 20261014000100_align_backend_db_contract.sql   callback_token_hash, pinecone_vector_id
│   └── 20261015000000_sync_with_backend_contract.sql  final alignment with the backend (see below)
├── manual/
│   └── drop_legacy_pgvector_column.sql   OPTIONAL, DESTRUCTIVE, never run by the CLI
├── tests/database/      pgTAP tests, run with `supabase test db`
│   ├── 01_schema_integrity.test.sql
│   ├── 02_row_level_security.test.sql
│   ├── 03_backend_contract.test.sql      replays the backend's real SQL statements
│   └── 04_repository_hardening.test.sql  SQL added to backend/db/repository.py (duplicate edges, stale jobs, ...)
└── seed.sql             Local seed (intentionally empty)
```

## Data model

```
auth.users (Supabase Auth: email/password, Google, JWTs, sessions)
   │ same UUID, ON DELETE CASCADE (trigger on_auth_user_created creates the row)
public.profiles (id, name)
   │
public.mind_maps.user_id   <- the only place ownership is stored
   ├── chat_threads (1 per map) ── chat_messages
   ├── generation_jobs ── seed_assets      (files live in Storage: seed-assets/<user_id>/<map_id>/<job_id>/<n>_<file>)
   ├── nodes ── edges                      (parent/child = directed edge source -> target; root = node with no incoming edge)
   └── embeddings                          (chunk text + Pinecone vector id; vectors live in Pinecone)
```

Triggers enforce what foreign keys cannot: every node, edge, job and embedding
references rows **of the same map**. A map's owner can never change. A
generation job follows a strict lifecycle (`pending -> running -> completed | failed`,
or `pending -> rejected`), and only one full-map job can be active per map.

### Who can access what

| Caller | How it connects | RLS |
|---|---|---|
| FastAPI backend | `DATABASE_URL` (asyncpg, `postgres` role) and `SUPABASE_SERVICE_ROLE_KEY` (Storage, `/me`) | Bypassed. The backend checks ownership itself (`assert_map_owner`). |
| Logged-in user via Supabase Data API (`authenticated`) | anon key + user JWT | Own profile, own maps/nodes/edges (read-write), own chat, jobs, seed assets and embeddings (read-only). Never `callback_token_hash`. Can only read their own `seed-assets/<user_id>/` files. |
| Anonymous (`anon`) | anon key | Only maps marked `is_public`, plus their nodes and edges (read-only). |
| LLM worker | none | No database access. |

The matching Python data-access layer is `backend/db/` (asyncpg): `repository.py`
holds every SQL statement the backend runs, and `schema.sql` is the same final
schema as these migrations in one script (for a fresh project set up by hand).

## What migration 20261015000000 changes and why

Every change is driven by the current backend/LLM code. **No table, column or row is dropped.**

| Change | Why (code reference) |
|---|---|
| `generation_jobs.status` `varchar(20)` -> `text` | `backend/app/api/internal.py` `report_fail()` uses `$2` both as the new status and in `CASE WHEN $2 = 'rejected'`. With asyncpg, Postgres inferred `varchar` and `text` for the same parameter and refused the statement (`42P08`). **Every worker failure callback returned 500**, and the job stayed `running`, so that map was stuck with "Generation already in progress" (409). The CHECK constraint still limits the column to the five allowed values. |
| `seed-assets` bucket limit 20 MB -> **25 MB**, MIME list = PDF/PNG/JPEG/WEBP, private | `backend/app/uploads/service.py` accepts up to 25 MB (`MAX_FILE_BYTES`) and only the types `sniff_mime()` returns. Files of 20–25 MB were failing with 502. |
| Storage: only `seed_assets_storage_select_own` (read your own folder) | Uploads and deletes are done by the backend with the service role. |
| `callback_token_hash` hidden from API users (column-level `GRANT SELECT`) | The column is backend-only, used to authenticate worker callbacks. Owners can still read job status and progress. |
| Dropped the `public.match_embeddings()` RPC and its HNSW index | Retrieval moved to Pinecone. `repository.hybrid_retrieve()` raises `NotImplementedError`, so nothing called the function, and it was an unnecessary public endpoint. |
| Added `idx_embeddings_pinecone_vector_id` | Matches `backend/db/schema.sql`. |
| Added `idx_mind_maps_user_id_updated`, `idx_generation_jobs_map_id_created` (replaces `idx_generation_jobs_map_id`), `idx_generation_jobs_triggered_by_message_id` | Serve `GET /api/maps` (`ORDER BY updated_at DESC`), the latest-job and active-job lookups, and the one unindexed foreign key. |
| `SET search_path = ''` on all trigger functions | Supabase security advisor (`function_search_path_mutable`). The function bodies are unchanged. |
| Renamed `users_pkey`/`users_id_fkey` -> `profiles_pkey`/`profiles_id_fkey` | Left over from the `users` -> `profiles` rename. Names now match `backend/db/schema.sql`. |

The deprecated column `public.embeddings.embedding` (pgvector) is **kept**,
because it may hold data in an existing project. The backend never touches it.
`manual/drop_legacy_pgvector_column.sql` removes it when you choose to. That
script refuses to run while any row still holds a vector, unless you
explicitly opt in.

## Applying the migrations

First check which case you are in: `supabase link --project-ref <ref>` and then
`supabase migration list`.

**Take a backup before touching a populated database:**
`supabase db dump --linked -f backup.sql`, or Dashboard -> Database -> Backups.

1. **New / empty project.** Run `supabase db push`. All 5 migrations run in order.
2. **Project already built from these migrations** (`migration list` shows the four 2026-09-30 / 2026-10-14 versions as applied remotely). Run `supabase db push`. Only `20261015000000` is applied. It preserves existing data.
3. **Project built by pasting `backend/db/schema.sql` (or other SQL) into the SQL Editor.** The tables exist but `migration list` shows no remote versions. Do **not** push directly, because the first migration would fail on existing tables. Mark the old versions as applied, then push:
   ```bash
   supabase migration repair --status applied 20260930000000 20260930000100 20261014000000 20261014000100
   supabase db push
   ```
   The new migration was tested on a database built from `backend/db/schema.sql` and reaches the same final schema.

Local development: `supabase start` (or `supabase db reset` to rebuild from migrations and `seed.sql`).

## Tests

```bash
supabase start          # or have a linked/local database running
supabase test db        # runs tests/database/*.sql with pgTAP
```

* `01_schema_integrity`: every column the backend uses, types, indexes, bucket config, integrity triggers, lifecycle CHECKs, cascades.
* `02_row_level_security`: owner, other user and anonymous access, column privileges, Storage folder isolation.
* `03_backend_contract`: the backend's SQL copied verbatim and `PREPARE`d with untyped parameters, as asyncpg does. It runs a full generate -> regenerate -> failures -> delete flow.
* `04_repository_hardening`: the statements `backend/db/repository.py` uses to survive real worker output and crashed workers (conflict-safe thread creation, duplicate edges skipped, stale-job sweep in `has_active_job()`, lifecycle-safe `update_job_status()`, Storage keys for map deletion), plus the quick-generate and node-regeneration reads.

## Environment variables (set in `backend/.env`, never committed)

| Variable | Where to find it |
|---|---|
| `SUPABASE_URL` | Dashboard -> Project Settings -> Data API (Project URL) |
| `SUPABASE_ANON_KEY` | Dashboard -> Project Settings -> API Keys (publishable / anon) |
| `SUPABASE_SERVICE_ROLE_KEY` | Dashboard -> Project Settings -> API Keys (secret / service_role). Server only. |
| `DATABASE_URL` | Dashboard -> Connect. Use the Session pooler or a direct connection. The Transaction pooler also works, because the backend sets `statement_cache_size=0`. |

The LLM worker needs **no** database credentials. `DATABASE_URL` in `llm/.env` is unused.
It only reads `SUPABASE_URL` (optional) to allow-list the Storage host for signed URLs.

For local CLI Google sign-in only: `SUPABASE_AUTH_EXTERNAL_GOOGLE_CLIENT_ID` and `SUPABASE_AUTH_EXTERNAL_GOOGLE_SECRET` (see `config.toml`).

## Dashboard settings (hosted project, not covered by migrations)

* **Authentication -> URL Configuration:** set Site URL to your frontend's URL. Add `<frontend>/auth/callback` (Google) and `<frontend>/reset-password` (password reset) to Redirect URLs.
* **Authentication -> Providers -> Google:** enable it and add the client ID and secret (FR-03).
* **Authentication -> Providers -> Email:** "Confirm email" is your choice. The backend reports `email_confirmation_required` either way.
* Storage bucket, RLS, policies and grants are all created by the migrations. No manual steps.
* Realtime is not required: the frontend polls `GET /api/maps/{id}/jobs/{job_id}`.

## Backend data layer (`backend/db/`) — what it handles for the database

These were open issues in earlier versions of this README. They are now handled
in `backend/db/repository.py` (no schema change needed; the database stays strict):

| Issue | Fix in `backend/db/` |
|---|---|
| `schema.sql` out of date (`status VARCHAR(20)` reproduced the `report_fail` 42P08 bug; old 20 MB bucket; missing indexes; `callback_token_hash` readable by API users) | `schema.sql` now equals the migrated schema (checked by building both and comparing every column, constraint, index, policy, trigger, function and grant). |
| Duplicate edges from the LLM (`uq_edges_source_target_label`) made `/internal/jobs/{id}/complete` fail with 500 and left the job `running` | `persist_generated_graph()` drops exact duplicates and self-loops and inserts edges with `ON CONFLICT DO NOTHING`. |
| `node_type` (varchar 50), `label` (255), edge `label` (100) and colours are unbounded in `NodeDraft` / `EdgeDraft` | Clipped / validated to the column limits before insert (invalid colours become NULL). |
| `get_or_create_chat_thread()` race on the first request for a map | `INSERT ... ON CONFLICT (map_id) DO NOTHING` + re-select. |
| A crashed worker (no callback) left the job `running` forever, so the map returned 409 "Generation already in progress" for good | `has_active_job()` closes generate/regenerate_full jobs older than `GENERATION_JOB_STALE_AFTER_S` (default 1800 s, well above `ARQ_JOB_TIMEOUT_S` = 300 s) in the same statement: never-started jobs become `rejected`, started ones `failed`. |
| `update_job_status()` could write a lifecycle-invalid row (e.g. `COMPLETED` without progress 100, `FAILED` before `started_at`) | Each target status now sets exactly what `chk_generation_job_lifecycle` requires. |
| Storage files were not removed when a map is deleted | `delete_mind_map()` deletes the map and returns its `seed-assets` keys, so a route can remove them with `admin.storage.from_("seed-assets").remove(keys)`. |

## Notes for other parts of the project (not changed here)

1. `backend/app/maps/routes.py` `delete_map` still runs its own `DELETE`; switching it to `db.repository.delete_mind_map()` and removing the returned keys from Storage would clean up uploaded files.
2. `backend/Dockerfile` copies only `app/`, and `asyncpg` is not listed in `backend/pyproject.toml`; the image needs both `db/` and `asyncpg` to reach the database.
3. The top-level and `backend/README.md` setup steps should prefer `supabase db push` over pasting `backend/db/schema.sql` (both now produce the same schema).
