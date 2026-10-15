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
│   └── 20261015000000_sync_with_backend_contract.sql  NEW: final alignment with the backend (see below)
├── manual/
│   └── drop_legacy_pgvector_column.sql   OPTIONAL, DESTRUCTIVE, never run by the CLI
├── tests/database/      pgTAP tests, run with `supabase test db`
│   ├── 01_schema_integrity.test.sql
│   ├── 02_row_level_security.test.sql
│   └── 03_backend_contract.test.sql      replays the backend's real SQL statements
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

## What the new migration (20261015000000) changes and why

Every change is driven by the current backend/LLM code. **No table, column or row is dropped.**

| Change | Why (code reference) |
|---|---|
| `generation_jobs.status` `varchar(20)` -> `text` | `routes/internal.py` `report_fail()` uses `$2` both as the new status and in `CASE WHEN $2 = 'rejected'`. With asyncpg, Postgres inferred `varchar` and `text` for the same parameter and refused the statement (`42P08`). **Every worker failure callback returned 500**, and the job stayed `running`, so that map was stuck with "Generation already in progress" (409). The CHECK constraint still limits the column to the five allowed values. |
| `seed-assets` bucket limit 20 MB -> **25 MB**, MIME list = PDF/PNG/JPEG/WEBP, private | `uploads/service.py` accepts up to 25 MB (`MAX_FILE_BYTES`) and only the types `sniff_mime()` returns. Files of 20–25 MB were failing with 502. |
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

## Issues outside `supabase/` (not changed here)

These need small code changes in `backend/` or `llm/`. The database is deliberately left strict.

1. **`backend/db/schema.sql` is out of date** and is still referenced as a setup script in the top-level `README.md`. It declares `generation_jobs.status VARCHAR(20)`, which reproduces the `report_fail` bug above. It also lacks the 25 MB bucket limit and the new indexes. *Minimum change:* point the README at `supabase db push`, and change `status VARCHAR(20)` to `status TEXT` in `schema.sql` (or regenerate it from the migrations).
2. **Duplicate edges from the LLM.** `llm/validation/schema.py` does not reject two identical edges (same source, target and label), but the database does (`uq_edges_source_target_label`). Such a result makes `/internal/jobs/{id}/complete` fail with 500 and leaves the job `running`. *Minimum change:* de-duplicate edges in `resolve_to_records()`, or before `persist_generated_graph()` in `routes/internal.py`.
3. **`node_type` length.** `nodes.node_type` is `varchar(50)`, but `NodeDraft.node_type` / `_DraftNode.node_type` are unbounded strings. *Minimum change:* add `max_length=50` to `_DraftNode.node_type`.
4. **Storage files are not removed when a map is deleted.** `DELETE /api/maps/{id}` cascades all rows, but the files under `seed-assets/<user_id>/<map_id>/` remain. Storage objects should be removed through the Storage API, not SQL. *Minimum change:* in `delete_map`, list and `admin.storage.from_("seed-assets").remove(...)` that prefix.
5. **Minor:** `get_or_create_chat_thread()` can race on the first request for a map (unique `chat_threads.map_id`). `INSERT ... ON CONFLICT (map_id) DO NOTHING` plus a re-select would make it idempotent.
