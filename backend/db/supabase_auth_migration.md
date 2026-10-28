# Mind Map Generator — Migrating to Supabase Auth

Group 15 · IT314 Software Engineering

Companion SQL: [schema.sql](./schema.sql) (same final schema as [`supabase/migrations/`](../../supabase/migrations/), which is the source of truth).

> **Status (Oct 2026).** This is the design record for the switch to Supabase Auth. It still describes the
> identity model, tables and RLS correctly. Two things changed afterwards:
> - **Vectors moved to Pinecone.** `embeddings` keeps chunk text + `pinecone_vector_id` for provenance only;
>   the pgvector column, HNSW index and `match_embeddings()` are retired (migration `20261015000000`).
> - **Auth goes through the FastAPI backend** (`backend/app/auth/service.py`: `/auth/login`, `/auth/signup`,
>   `/auth/google`), which calls Supabase Auth and sets HttpOnly cookies; the frontend does not use supabase-js.
>   The backend reaches Postgres with asyncpg (`backend/db/`), and Storage/`/me` with the service-role client.
> Where §3–§5 below say otherwise, the code and `supabase/README.md` win.

---

## 1. Architecture decision

```
Supabase Auth  (sign-up, login, Google OAuth, JWTs, refresh tokens, sessions)
      │
auth.users.id  ─────────────── one UUID per person (source of truth)
      │  same UUID, 1:1, ON DELETE CASCADE
public.profiles.id             app-specific user info (display name, timestamps)
      │  ON DELETE CASCADE
public.mind_maps.user_id       ← the ONLY place ownership is stored
      │  map_id
      ├── chat_threads ── chat_messages
      ├── generation_jobs ── seed_assets
      ├── nodes ── edges
      └── embeddings
```

**The one architecture we chose:** application tables reference **`public.profiles(id)`**, and `profiles.id` **is** `auth.users.id`. So `mind_maps.user_id` always holds the Supabase user's UUID. No second user ID exists.

| Layer | Manages | Who writes it |
|---|---|---|
| **Supabase Auth** (the service) | Sign-up, login, password hashing, email confirmation, Google OAuth, issuing JWT access tokens, refresh-token rotation, logout | Supabase |
| **`auth.users`** (table in the `auth` schema) | The identity record: `id`, `email`, `encrypted_password`, `email_confirmed_at`, linked OAuth identities (`auth.identities`), `raw_user_meta_data` | Supabase only — never write it yourself |
| **`public.profiles`** | Project-specific user data: display `name`, `created_at`, `updated_at`. Future app data (preferences etc.) goes here | Auto-created by a trigger on sign-up; user can edit `name` |
| **Application tables** | Mind maps, chat, generation jobs, seed assets, nodes, edges, embeddings | FastAPI backend (and, for canvas edits, the user under RLS) |

**Why keep `profiles` instead of pointing everything straight at `auth.users`?**
- `auth` is Supabase's internal schema. It is not exposed through the Data API and you should not add columns to it. `profiles` gives you a normal table you control.
- Same UUID (`id REFERENCES auth.users(id) ON DELETE CASCADE`) means no mapping table, no "which ID is this?" confusion, and `auth.uid()` can be compared directly with `mind_maps.user_id`.
- `ON DELETE CASCADE` means deleting a Supabase user (account deletion, or Admin removing a user — FR‑26) automatically deletes their profile and all their maps and child data.
- `mind_maps` references `profiles`, not `auth.users`, so the app only depends on its own schema. Because the profile is created by a trigger in the same transaction as the auth user, it always exists before any map is created.

**Why the `sessions` table is no longer needed:** it stored refresh-token hashes and expiry/revocation. Supabase Auth already does exactly this (`auth.sessions`, `auth.refresh_tokens`), issues short-lived JWTs, rotates refresh tokens, and handles logout. Keeping our own copy would duplicate security-critical logic and could get out of sync. The backend is stateless: it just verifies the JWT on each request. (The Redis cache mentioned in v2 for the session hot path is also no longer needed.)

**Why `email` was removed from the app table:** `auth.users.email` is the source of truth and can change (email change flow). A copy in `profiles` would go stale. The backend gets the email from the verified JWT (`email` claim) when needed.

---

## 2. Final table structure

| Table | Important columns | PK | Foreign keys | Important constraints | Purpose |
|---|---|---|---|---|---|
| **profiles** *(was users)* | `name`, `created_at`, `updated_at` | `id` UUID | `id → auth.users(id)` ON DELETE CASCADE | — | App-level user info; 1:1 with Supabase user |
| ~~sessions~~ | — | — | — | — | **Removed** (Supabase Auth) |
| **mind_maps** | `user_id` (DEFAULT `auth.uid()`), `title`, `description`, `is_public` | `id` | `user_id → profiles(id)` CASCADE | owner immutable (trigger); index `(user_id, created_at DESC)` | A user-owned mind map; **ownership source of truth** |
| **chat_threads** | `map_id` (UNIQUE), `user_id` | `id` | `map_id → mind_maps` CASCADE; `user_id → profiles(id)` CASCADE | trigger: `user_id` must equal map owner | One chat/LangGraph thread per map |
| **chat_messages** | `thread_id`, `role`, `content` | `id` | `thread_id → chat_threads` CASCADE | `role IN ('user','assistant')` | Chat transcript |
| **generation_jobs** | `map_id`, `triggered_by_message_id`, `job_type`, `target_node_id`, `status`, `progress_percent`, timestamps | `id` | `map_id → mind_maps` CASCADE; `triggered_by_message_id → chat_messages` SET NULL; `target_node_id → nodes` SET NULL (deferrable) | lifecycle CHECK, timestamp CHECK, one active full-map job per map, **target rule fixed** (see §4) | Async LLM job tracking + progress (NFR‑12) |
| **seed_assets** | `generation_job_id`, `asset_type`, `storage_key`, `content_text`, … | `id` | `generation_job_id → generation_jobs` CASCADE | storage/size/checksum CHECKs | Uploaded PDFs/images/text for a seed; files in private Storage bucket |
| **nodes** | `map_id`, `label`, `pos_x/y`, colours, `width/height`, `node_type`, `source_job_id`, `node_data` | `id` | `map_id → mind_maps` CASCADE; `source_job_id → generation_jobs` SET NULL | colour/dimension CHECKs; same-map trigger | Mind-map concepts |
| **edges** | `map_id`, `source_node_id`, `target_node_id`, `label`, `edge_data` | `id` | `map_id → mind_maps`; both node FKs → `nodes` CASCADE | no self-loop; unique (src, tgt, label) NULLS NOT DISTINCT; same-map trigger | Relationships between nodes |
| **embeddings** | `generation_job_id`, `map_id`, `chunk_text`, `pinecone_vector_id`, `embedding_model`, `source`, `source_url` | `id` | job → `generation_jobs` CASCADE; `map_id → mind_maps` CASCADE | source/url CHECK; same-map trigger | Provenance of RAG chunks (vectors live in Pinecone, namespace = `map_id`) |

Unchanged: every other column, CHECK constraint, index and all same-map integrity triggers.

---

## 3. Complete SQL

Preferred: `supabase db push` (applies `supabase/migrations/`). Alternative for a fresh project: run **[schema.sql](./schema.sql)** once in the Supabase SQL Editor. Sections:

0. `set_updated_at` trigger · 1. `profiles` + sign-up trigger · 2. (sessions removed) · 3–10. tables · 11. same-map integrity triggers · 12. RLS helpers + policies · 13. table/column privileges · 14. private Storage bucket · 15. `langgraph` schema · 16. design notes.

### RLS policies explained

Ownership rule used everywhere: **you own a row if you own its mind map** (`mind_maps.user_id = auth.uid()`). Helper `private.is_map_owner(map_id)` answers that; `private.is_public_map(map_id)` answers "is this map public?". They live in a `private` schema so they can't be called as REST endpoints.

| Table | SELECT | INSERT | UPDATE | DELETE | Why |
|---|---|---|---|---|---|
| profiles | own row (`id = auth.uid()`) | own row only (fallback; trigger normally does it) | own row, **`name` column only** | none (delete the auth user instead → cascade) | Users see/edit only themselves |
| mind_maps | own maps; **plus** any map with `is_public = true` (anon + authenticated) | only with `user_id = auth.uid()` | own maps; only `title`, `description`, `is_public` | own maps | Owner has full control; can't create maps for others or transfer them |
| nodes | if you own the map; or map is public | if you own the map | if you own the map; not `map_id`/`source_job_id` | if you own the map | Canvas editing (FR‑11…13) directly from the frontend |
| edges | same as nodes | same | same (can reconnect endpoints) | same | Same; the same-map trigger also stops edges pointing into other maps |
| chat_threads | if you own the map | **backend only** | backend only | cascade only | Prevents users forging threads |
| chat_messages | if you own the thread's map | **backend only** | backend only | cascade only | Prevents forged "assistant" messages |
| generation_jobs | if you own the map | **backend only** | backend only | cascade only | Users can watch progress (e.g. Realtime) but can't queue jobs directly (bypassing rate limits, DR‑01) or fake "completed" |
| seed_assets | if you own the job's map | **backend only** | backend only | cascade only | Backend parses uploads (DR‑02) |
| embeddings | if you own the map | **backend only** | backend only | cascade only | Owner may view sources; vectors written by the pipeline |

For a **public** map, only `mind_maps`, `nodes` and `edges` are readable by others. Chat history, uploads, jobs and embeddings stay private (Conflict #5: raw ideas stay private).

---

## 4. Migration / change summary (OLD → NEW)

| OLD (v2) | NEW (v3) | Reason |
|---|---|---|
| `users` table | renamed **`profiles`** | App data only; identity lives in `auth.users` |
| `users.id UUID DEFAULT gen_random_uuid()` | `profiles.id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE` (no default) | Same UUID as the Supabase user |
| `users.email` | removed | `auth.users.email` / JWT `email` claim |
| `users.hashed_password` | removed | Supabase Auth hashes/stores passwords |
| `users.oauth_provider`, `users.oauth_id` | removed | Supabase `auth.identities` stores Google links |
| `users.email_verified_at` | removed | `auth.users.email_confirmed_at` |
| `users.name` | kept as `profiles.name` | Display name (filled from Google `full_name` / sign-up metadata) |
| `chk_users_oauth_pair`, `chk_users_auth_method`, `chk_users_oauth_provider` | removed | Columns gone |
| `uq_users_email_lower`, `uq_users_oauth` | removed | Supabase enforces unique emails/identities |
| `sessions` table + indexes | **removed** | Supabase Auth handles sessions & refresh tokens |
| `mind_maps.user_id → users(id)` | `→ profiles(id)`, `DEFAULT auth.uid()` | = `auth.users.id` |
| `chat_threads.user_id → users(id)` | `→ profiles(id)` + trigger: must equal map owner | Only denormalized owner column; can't drift |
| — | trigger `trg_mind_maps_owner_immutable` | RLS relies on `user_id`; owner never changes |
| — | `handle_new_user()` trigger on `auth.users` + backfill | Profile auto-created on sign-up/first Google login |
| `chk_target_node_matches_job_type`: regenerate_node ⇔ target NOT NULL | regenerate_node **or** target IS NULL; "target required" moved to INSERT check in trigger | **Bug fix**: v2 made it impossible to delete a node targeted by a node-regen job, the map, or the user (FK `ON DELETE SET NULL` violated the CHECK). Reproduced in testing |
| `CREATE EXTENSION vector` | `... WITH SCHEMA extensions`; column `extensions.vector(1536)` | Supabase convention |
| functions without `search_path` | `SET search_path = ''` + schema-qualified names | Supabase security linter / search-path hijack protection |
| no RLS | RLS on all 9 tables + policies (§3) | Supabase exposes `public` via the Data API |
| default grants (ALL to anon/authenticated) | explicit per-table and per-column grants | Defence in depth |
| "object storage" | private Storage bucket `seed-assets`, owner-folder read policy | Uploads never public |
| LangGraph tables in default schema | `langgraph` schema, not exposed | Otherwise auto-exposed without RLS |

---

## 5. Backend implications (FastAPI)

### Who does what

```
Browser (supabase-js, publishable/anon key)
   │ 1. signUp / signInWithPassword / signInWithOAuth({provider:'google'})
   ▼
Supabase Auth ──► returns session { access_token (JWT), refresh_token }
   │ 2. every API call:  Authorization: Bearer <access_token>
   ▼
FastAPI ──► 3. verify JWT → user_id = claims["sub"]
        ──► 4. query Postgres, always scoped to user_id
```

- **Sign-up (FR‑01):** the **frontend** calls `supabase.auth.signUp({ email, password, options: { data: { name } } })`. FastAPI has **no** `/signup` endpoint, no password hashing. The DB trigger creates the profile automatically.
- **Login (FR‑02):** frontend `supabase.auth.signInWithPassword(...)`. Remove FastAPI `/login`, `/refresh`, `/logout`, token creation and the sessions/Redis code. supabase-js refreshes tokens automatically.
- **Google OAuth (FR‑03, FR‑39):** enable Google in *Supabase Dashboard → Authentication → Providers* (paste Google client ID/secret there, add the Supabase callback URL in Google Cloud). Frontend: `supabase.auth.signInWithOAuth({ provider: 'google' })`. First Google login creates the `auth.users` row → trigger creates the profile with the Google name. FastAPI does nothing special.
- **Getting the user's UUID:** a FastAPI dependency verifies the JWT and returns `sub`:

```python
# auth.py
import jwt  # pip install "pyjwt[crypto]"
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

SUPABASE_URL = settings.SUPABASE_URL
jwks = jwt.PyJWKClient(f"{SUPABASE_URL}/auth/v1/.well-known/jwks.json")
bearer = HTTPBearer()


def current_user_id(cred: HTTPAuthorizationCredentials = Depends(bearer)) -> str:
    token = cred.credentials
    try:
        key = jwks.get_signing_key_from_jwt(token).key
        claims = jwt.decode(
            token,
            key,
            algorithms=["ES256", "RS256"],
            audience="authenticated",
            issuer=f"{SUPABASE_URL}/auth/v1",
        )
    except jwt.PyJWTError:
        raise HTTPException(401, "Invalid or expired token")
    return claims["sub"]  # == auth.users.id == profiles.id
```
  *(Older projects that still use the legacy shared JWT secret verify with `algorithms=["HS256"]` and the project's JWT secret instead of JWKS. Alternative for beginners: `supabase.auth.get_user(token)` — simpler but makes a network call per request.)*

- **Creating / fetching the profile:** the trigger creates it, so the backend only reads it: `SELECT id, name FROM profiles WHERE id = :uid`. `PATCH /me` updates `name`. (Optional safety net: `INSERT INTO profiles(id) VALUES (:uid) ON CONFLICT DO NOTHING`.)
- **Creating a mind map:**
```python
@router.post("/maps")
def create_map(body: MapIn, uid: str = Depends(current_user_id), db=Depends(get_db)):
    map_id = db.execute(
        text("INSERT INTO mind_maps (user_id, title) VALUES (:uid, :title) RETURNING id"),
        {"uid": uid, "title": body.title},
    ).scalar_one()
    db.execute(
        text("INSERT INTO chat_threads (map_id, user_id) VALUES (:m, :uid)"),
        {"m": map_id, "uid": uid},
    )
    db.commit()
    return {"id": map_id}
```
  **Never** take `user_id` from the request body — always from the verified token.

- **`auth.uid()` vs the backend:** `auth.uid()` reads the JWT that PostgREST puts into the DB session. A FastAPI **direct** Postgres connection has no JWT, so there `auth.uid()` is `NULL` and **RLS is bypassed** (the `postgres` role owns the tables). Therefore the backend must add `WHERE user_id = :uid` (or check map ownership) to every query. Use one reusable check:
```python
def get_owned_map(map_id: UUID, uid=Depends(current_user_id), db=Depends(get_db)):
    ok = db.execute(
        text("SELECT 1 FROM mind_maps WHERE id=:m AND user_id=:u"), {"m": map_id, "u": uid}
    ).first()
    if not ok:
        raise HTTPException(404, "Map not found")  # 404, don't reveal existence
    return map_id
```
  Every route that touches nodes/edges/jobs/chat/embeddings depends on `get_owned_map`. Vector search must include `WHERE map_id = :m` (`ORDER BY embedding <=> :q LIMIT k`).
  *(Optional, advanced: run user-request queries inside a transaction with `SET LOCAL ROLE authenticated; SELECT set_config('request.jwt.claims', :claims_json, true);` so RLS is enforced for the backend too.)*

- **Direct PostgreSQL or Supabase APIs?** Recommended: **FastAPI connects directly to Postgres** (SQLAlchemy/asyncpg with the Supabase connection string). You need this anyway for transactions, pgvector queries, the triggers, and the LangGraph Postgres checkpointer. Use the Supabase Python client only for **Storage uploads** and **Auth admin** tasks (e.g. deleting a user). The frontend uses supabase-js for **Auth** (and optionally read-only queries / Realtime job progress, which RLS makes safe).
- **LangGraph checkpointer:** connect it with `options=-c search_path=langgraph` (direct or session-pooler connection) so its tables are created in the private `langgraph` schema.

### Where the keys go

| Secret | Where it may live | Never |
|---|---|---|
| Publishable / **anon** key | Frontend (it is public by design; RLS protects data) | — |
| **Service role** / secret key | FastAPI server env vars / secret manager only; used for Storage uploads & Auth admin | Frontend code, `VITE_*`/`NEXT_PUBLIC_*` vars, mobile apps, Git, logs |
| Database connection string (postgres password) | FastAPI server env only | Frontend, Git |
| Google OAuth client secret | Supabase dashboard only | Your code/repo |

Both the service-role key and the postgres connection **bypass RLS** — anyone holding them can read every user's maps.

---

## 6. Security review (issues found → fixed in the SQL)

| # | Risk | Fix in final SQL |
|---|---|---|
| 1 | Supabase exposes every `public` table via REST with the public anon key → anyone could read/modify all data | RLS enabled on all 9 tables; explicit per-table/column grants; anon limited to SELECT on public maps |
| 2 | User reads/edits another user's mind map | Every policy checks `mind_maps.user_id = auth.uid()` (directly or via parent) — verified by tests |
| 3 | FK checks ignore RLS → user links their node/edge/job to **another user's** node/job UUID | Same-map triggers reject any cross-map reference (tested) |
| 4 | User transfers a map / moves a node into someone else's map | `UPDATE` grants exclude `user_id`/`map_id`; `WITH CHECK`; owner-immutable trigger |
| 5 | `chat_threads.user_id` disagrees with map owner | Trigger enforces equality; policies use map ownership only |
| 6 | Users forge assistant messages, "completed" jobs, or insert pending jobs to bypass rate limits | Chat/jobs/seed_assets/embeddings are read-only via Data API |
| 7 | Accidental public exposure | `is_public` defaults FALSE; public maps expose only map/nodes/edges; chat, uploads, embeddings never public; Storage bucket private |
| 8 | Service-role / DB credentials leak | Documented: server-only; backend must scope queries by verified `sub` |
| 9 | `SECURITY DEFINER` functions abused / search-path hijack | `SET search_path = ''`, helpers in unexposed `private` schema, `EXECUTE` revoked from anon where not needed |
| 10 | LangGraph checkpoint tables auto-exposed without RLS | Dedicated `langgraph` schema, no anon/authenticated access |
| 11 | Duplicate auth logic (passwords, OAuth ids, sessions) | All removed |
| 12 | Authorization based on user-editable data | `raw_user_meta_data` is only used for the display name — never for roles/permissions (users can change it) |
| 13 | Sign-up trigger error blocks all sign-ups | Trigger is minimal, trims/truncates name, `ON CONFLICT DO NOTHING` |
| 14 | Deleting a node/map/account failed (v2 CHECK vs `ON DELETE SET NULL`) | Constraint fixed (see §4) |
| 15 | Backend bypasses RLS | `get_owned_map` pattern; vector search always filtered by `map_id` |

### Assumptions
- Fresh Supabase project (Postgres 15+, pgvector ≥ 0.5 for HNSW, `extensions` schema). If `vector` was previously enabled in `public`, change `extensions.vector` to `vector`.
- Backend writes chat, jobs, seed assets and embeddings; frontend may edit maps/nodes/edges directly or via FastAPI.
- `is_public` means "read-only, viewable by anyone". Sharing with specific users/editing permissions (FR‑20…25) and admin roles (FR‑26/27) are Sprint 3 and not modeled yet; add them by extending `private.is_map_owner()` (e.g. a `map_members` table) rather than editing every policy. For admin, use `app_metadata` (not user-editable) or an admin table.
- Storage path convention: `<user_id>/<map_id>/<job_id>/<file>`.

### Verification performed
The SQL was executed on a local PostgreSQL + pgvector instance with a mock of Supabase's `auth`/`storage` schemas and `anon`/`authenticated`/`service_role` roles. 46 automated checks passed: sign-up trigger, owner CRUD, cross-user read/write denial on every table, anon access, cross-map reference blocking, pgvector query, Storage policy, node/map/account deletion cascades, and reproduction of the v2 delete bug. Still run the script once in your real Supabase SQL Editor to confirm.
