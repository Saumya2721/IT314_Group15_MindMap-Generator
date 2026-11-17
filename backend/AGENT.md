# Backend Agent Instructions (`backend/AGENT.md`)

This guide outlines rules, architectural constraints, and verification protocols that any AI coding assistant must review before making modifications to `backend/`.

---

## 1. Scope & System Boundaries

- **Separation of Concerns**: The backend owns PostgreSQL persistence, Supabase authentication, file intake into Supabase Storage, and job queue dispatching.
- **Never Run LLM Synthesis in Request Handlers**: All generation tasks must be enqueued via `app.services.queue.enqueue_generation_job` to the Redis ARQ queue. Handlers must respond immediately with the `job_id`.
- **Worker Detachment**: The worker communicates back only through authenticated callback routes in `app/routes/internal.py`.

---

## 2. Non-Negotiable Invariants

### A. Dual-Defense Authentication Protocol
- **Cookie & Bearer Hybrid**: Authentication middleware (`app/core/supabase.py::current_user_id`) must check:
  1. `Authorization: Bearer <token>` header (primary for API / active tab sessions).
  2. `access_token` HttpOnly cookie (fallback for browser restoration).
- **Session Tokens**: Login, registration, and refresh endpoints must set both `access_token` and `refresh_token` as `HttpOnly`, `SameSite=Lax`, `Secure` (in production) cookies, while also returning the session object in the response JSON.

### B. Generation Job Lifecycle State Machine (`chk_generation_job_lifecycle`)
- PostgreSQL table `public.generation_jobs` enforces strict lifecycle constraints:
  - Jobs that have started (`started_at IS NOT NULL`) **cannot** have status `rejected`. Terminal state must be `failed`.
  - Only jobs that were never picked up (`started_at IS NULL`) may have status `rejected`.
- Internal completion/failure endpoints in `app/routes/internal.py` must strictly uphold this logic before updating `generation_jobs`.

### C. Supabase Storage Cleanup & Column Correctness
- **Table Schema**: Table `public.seed_assets` defines the column as **`storage_key`** (type `TEXT`). Column `storage_path` does **not** exist.
- **Fail-Safe Cleanup on Map Deletion**: In `DELETE /api/maps/{map_id}`, `delete_map_storage_files` must execute **before** `DELETE FROM public.mind_maps`.
- **No Orphaned Storage Objects**: If `delete_map_storage_files` fails, the exception must propagate and abort the route with `502 Bad Gateway`. The database row must never be deleted if storage cleanup fails.

### D. Multi-User Authorization & RLS
- All user-facing routes must enforce ownership check via `assert_map_owner(pool, map_id, uid)`.
- Users must never be able to inspect, mutate, or delete another user's mind maps or jobs.

### E. Snapshot Schema Alignment & OCC Locks
- Database table `map_snapshots` stores graphs in `graph`, `nodes_snapshot`, and `edges_snapshot` JSONB columns with `version_no`. Endpoints must compute and return `nodes_count` and `edges_count`.
- Graph updates via `PATCH /api/maps/{id}/graph` require `expected_version`. If the client's version does not match the database version, return `409 Conflict`.
- Restoring a snapshot bumps `map_version` and returns `new_version` so clients can synchronize their OCC refs.

### F. Topological Edge Integrity & Cycle Prevention
- `PATCH /api/maps/{id}/edges/{edge_id}` must reject self-loops (`source_node_id == target_node_id`) with HTTP 400.
- When updating edge connections, run `check_edge_creates_cycle(other_edges, new_src, new_tgt)` and reject cycles with HTTP 400.

### G. Chat Action Proposals & Root Node Protection
- Action proposals confirming edits must take an automated safety snapshot (`create_map_snapshot`) before applying modifications.
- Proposals attempting to delete a root node (`node_type == 'root'`) must be rejected with HTTP 400.

---

## 3. Environment & Execution Specifics

- **Working Directory**: Always set location to `backend/` before running tests or linters:
  ```powershell
  Set-Location "backend"
  ```
- **Module Resolution**: The project relies on `app` being in `sys.path`. Running commands from the repo root will fail unless `PYTHONPATH=backend/app` is exported.
- **Ruff FastAPI Idiom**: FastAPI parameter defaults like `= Depends(...)`, `= Form(...)`, and `= File(...)` are valid and expected. Rule `B008` is explicitly ignored in `pyproject.toml`.

---

## 4. Verification Quality Checklist

Before finalizing any changes to `backend/`, execute all four quality gates:

```powershell
# 1. Formatting
python -m ruff format --check app

# 2. Linting (Must report 0 errors)
python -m ruff check app

# 3. Static Type Analysis (Must report 0 errors across 24 source files)
python -m mypy app

# 4. Unit Test Suite (Must report 100% passed)
python -m pytest tests -q
```

