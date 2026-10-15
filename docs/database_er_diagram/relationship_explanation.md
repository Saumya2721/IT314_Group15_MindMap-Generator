# Relationship explanation — Mind Map Generator database

Source of truth: the PostgreSQL catalog of a database built from `supabase/migrations` (final migration `20261015000000_sync_with_backend_contract.sql`), cross-checked against the backend and LLM code (automated cross-check: 886/886 checks passed).

**Entities (11):** 9 application tables in schema `public` — `profiles`, `mind_maps`, `chat_threads`, `chat_messages`, `generation_jobs`, `seed_assets`, `nodes`, `edges`, `embeddings` — plus 2 Supabase-managed tables the app depends on, shown with dashed borders and only the columns the app uses: `auth.users` (Supabase Auth) and `storage.objects` (Supabase Storage). There is no separate authentication table.

**How to read cardinality:** "1 : 0..N" means one parent row relates to zero or more child rows, and each child row belongs to exactly one parent. "0..1" on the parent side means the child's foreign key is nullable (the child may have no parent).

## Relationships

| # | Parent (one side) | Child | Foreign key | Cardinality (parent : child) | Type | Participation | ON DELETE | Enforced by | Meaning |
|---|---|---|---|---|---|---|---|---|---|
| 1 | auth.users | profiles | profiles.id → auth.users.id | 1 : 0..1 | one-to-one (optional child) | child mandatory; parent optional | CASCADE | Database FK | Every Supabase Auth user gets exactly one profile row with the same UUID. The FK allows at most one; the database trigger on_auth_user_created creates it on sign-up, so in practice it is 1:1. |
| 2 | profiles | mind_maps | mind_maps.user_id → profiles.id | 1 : 0..N | one-to-many | child mandatory; parent optional | CASCADE | Database FK | A user owns any number of mind maps; every map has exactly one owner. Ownership cannot change (trigger trg_mind_maps_owner_immutable). |
| 3 | mind_maps | chat_threads | chat_threads.map_id → mind_maps.id | 1 : 0..1 | one-to-one (optional child) | child mandatory; parent optional | CASCADE | Database FK | A mind map has at most one chat thread (UNIQUE map_id); the backend creates it on the first generate/regenerate request. |
| 4 | profiles | chat_threads | chat_threads.user_id → profiles.id | 1 : 0..N | one-to-many | child mandatory; parent optional | CASCADE | Database FK | Denormalised owner of a conversation. Trigger trg_chat_threads_owner_matches_map guarantees it equals the owner of the thread's map. |
| 5 | chat_threads | chat_messages | chat_messages.thread_id → chat_threads.id | 1 : 0..N | one-to-many | child mandatory; parent optional | CASCADE | Database FK | A thread contains any number of user/assistant messages, ordered by created_at. |
| 6 | mind_maps | generation_jobs | generation_jobs.map_id → mind_maps.id | 1 : 0..N | one-to-many | child mandatory; parent optional | CASCADE | Database FK | A map has any number of generation jobs (history). At most one generate/regenerate_full job can be pending/running per map (partial unique index). |
| 7 | chat_messages | generation_jobs | generation_jobs.triggered_by_message_id → chat_messages.id | 0..1 : 0..N | one-to-many (optional parent) | child optional; parent optional | SET NULL | Database FK | The user chat message that started a job. Optional in the schema (SET NULL on delete); the backend always sets it for generate and regenerate jobs. |
| 8 | generation_jobs | seed_assets | seed_assets.generation_job_id → generation_jobs.id | 1 : 0..N | one-to-many | child mandatory; parent optional | CASCADE | Database FK | The seed material (uploaded PDF/image text, or typed topic/ideas) a job generated from. Written by the backend when the LLM worker reports completion. |
| 9 | storage.objects | seed_assets | seed_assets.storage_key → storage.objects.name | 0..1 : 0..1 | one-to-one (optional both sides) | child optional; parent optional | — | Application only (no FK) | For pdf/image seed assets, storage_key holds the object path in the private 'seed-assets' bucket. Set by backend/uploads/service.py; there is no foreign key, and deleting a map does not delete the file. Text seeds have no file (CHECK chk_seed_asset_storage). |
| 10 | generation_jobs | embeddings | embeddings.generation_job_id → generation_jobs.id | 1 : 0..N | one-to-many | child mandatory; parent optional | CASCADE | Database FK | Retrieval chunks recorded for a job. Table and repository helpers exist, but the current worker stores vectors only in Pinecone and no backend route writes this table. |
| 11 | mind_maps | embeddings | embeddings.map_id → mind_maps.id | 1 : 0..N | one-to-many | child mandatory; parent optional | CASCADE | Database FK | Chunks belong to a map (Pinecone namespace = map_id). A trigger ensures the chunk's job belongs to the same map. |
| 12 | mind_maps | nodes | nodes.map_id → mind_maps.id | 1 : 0..N | one-to-many | child mandatory; parent optional | CASCADE | Database FK | A mind map contains any number of nodes (concepts). |
| 13 | generation_jobs | nodes | nodes.source_job_id → generation_jobs.id | 0..1 : 0..N | one-to-many (optional parent) | child optional; parent optional | SET NULL | Database FK | The job that produced a node. Optional: NULL for nodes added by hand on the canvas, or after the job is deleted (SET NULL). Trigger checks the job is on the same map. |
| 14 | nodes | generation_jobs | generation_jobs.target_node_id → nodes.id | 0..1 : 0..N | one-to-many (optional parent) | child optional; parent optional | SET NULL (deferrable) | Database FK | For job_type = 'regenerate_node' only: the node being regenerated. Present in the schema, but the backend currently answers node regeneration with HTTP 501, so no such jobs are created. |
| 15 | mind_maps | edges | edges.map_id → mind_maps.id | 1 : 0..N | one-to-many | child mandatory; parent optional | CASCADE | Database FK | A mind map contains any number of edges. |
| 16 | nodes | edges | edges.source_node_id → nodes.id | 1 : 0..N | one-to-many | child mandatory; parent optional | CASCADE | Database FK | Edge start node. In generated maps this is the parent concept. |
| 17 | nodes | edges | edges.target_node_id → nodes.id | 1 : 0..N | one-to-many | child mandatory; parent optional | CASCADE | Database FK | Edge end node. In generated maps this is the child concept. |

## Many-to-many and self-referencing relationships

- **nodes ↔ nodes (recursive many-to-many, via `edges`).** No table has a foreign key to itself. Instead, `edges` is an associative entity with two foreign keys to `nodes` (`source_node_id`, `target_node_id`), so a node can be linked to many other nodes and vice versa. Together these two relationships (rows 16–17) form the mind-map graph; in generated maps each edge points from a parent concept to a child concept.
- **generation_jobs ↔ nodes (two independent relationships).** A job *generated* many nodes (`nodes.source_job_id`), and a node may be the *regeneration target* of many jobs (`generation_jobs.target_node_id`). These are two separate one-to-many relationships, not one many-to-many.
- No other many-to-many relationships exist.

## Enforced by the database

- **Primary keys** — every table has a single-column UUID primary key `id`. `profiles.id` is also the foreign key to `auth.users.id` (shared primary key, 1:1).
- **16 foreign keys** — all rows in the relationship table marked *Database FK*.
- **UNIQUE `chat_threads.map_id`** — makes mind map → chat thread one-to-one.
- **UNIQUE `edges (source_node_id, target_node_id, label)` NULLS NOT DISTINCT** — no duplicate edge between the same two nodes with the same label (two unlabelled duplicates also collide).
- **Partial UNIQUE `generation_jobs (map_id)` WHERE status ∈ {pending, running} AND job_type ∈ {generate, regenerate_full}** — at most one active full-map generation per map.
- **CHECK constraints** — allowed values (role, job_type, status, asset_type, source), the job lifecycle (status ↔ timestamps/progress/error), no self-loop edges, #RRGGBB colours, seed-asset storage rules, checksum format.
- **Triggers** — `on_auth_user_created` creates the profile on sign-up; same-map triggers on nodes, edges, generation_jobs and embeddings stop a row from pointing at a node/job/message of another map; `trg_chat_threads_owner_matches_map` keeps `chat_threads.user_id` equal to the map owner; `trg_mind_maps_owner_immutable` forbids changing a map's owner; `updated_at` triggers on profiles, mind_maps, nodes.

## Inferred from application code (not enforced by the schema)

- **seed_assets.storage_key → storage.objects.name** — the uploaded PDF/image is stored at `<user_id>/<map_id>/<job_id>/<n>_<file>` in the private `seed-assets` bucket (`backend/uploads/service.py`). No FK; deleting a map does not delete the file.
- **embeddings.pinecone_vector_id → Pinecone** — vectors live in the Pinecone index (namespace = map_id), outside PostgreSQL, so Pinecone is not drawn as an entity.
- **Edges are directed parent → child, the map has a single root and no cycles** — enforced by the LLM worker (`llm/validation/schema.py`: single root, connected, acyclic), not by the database. The database only forbids self-loops and duplicates.
- **Every generate/regenerate job has a trigger message** — the backend always sets `triggered_by_message_id`, but the column is nullable in the schema.
- **Profile is effectively mandatory (1:1)** — the FK alone allows a user without a profile (0..1); the database trigger creates it at sign-up.
- **Schema present but not exercised by the current code** — `regenerate_node` jobs / `generation_jobs.target_node_id` (node regeneration returns HTTP 501); the `embeddings` table (repository helpers exist, but the worker writes vectors only to Pinecone); `mind_maps.description` and `is_public` (no route sets them yet); `nodes.width/height` and `edges.edge_data` (read by the API; set only by canvas edits).
- **Deprecated column** — `embeddings.embedding vector(1536)` is kept for existing data, is always NULL for rows written by the current backend, and can be dropped with `supabase/manual/drop_legacy_pgvector_column.sql`.

## Security (Row Level Security) — explained separately, not part of the ER model

- Row Level Security is enabled on all nine tables; policies are not ER entities and are not drawn.
- Ownership has one source of truth, `mind_maps.user_id`; every child table proves ownership through its map.
- Through the Supabase Data API a user can read/write their own maps, nodes and edges, read their own chat, jobs, seed assets and embeddings, and read maps marked `is_public` (anonymous visitors too). `callback_token_hash` is never readable by API users.
- The FastAPI backend connects as the table owner / service role (bypasses RLS) and checks ownership itself (`assert_map_owner`). The LLM worker has no database access.
