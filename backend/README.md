# Mind Map Generator — Backend API

FastAPI service powering the Mind Map Generator. Handles user authentication, database persistence via PostgreSQL / Supabase, seed file uploads via Supabase Storage, and generation job dispatching via Upstash Redis / ARQ.

---

## Architecture & Responsibilities

The backend acts as the central orchestrator and security boundary for the system:
- **Authentication & RBAC**: Dual-Defense architecture combining secure `HttpOnly` session cookies with in-memory Bearer token fallback. Integrated with Supabase GoTrue Auth and PKCE Google OAuth with secure verifier persistence. Includes strict role-based access control (`profiles.role` = `user` | `admin`) via `require_admin_role()`.
- **Admin Oversight & Zero Content Access**: Provides user governance (suspension/unsuspension/deletion) and aggregate-only analytics. Strict privacy rule guarantees admins cannot read user mind maps, nodes, edges, chat threads, or memories. All actions log to `admin_audit_log`.
- **Long-Term Agent Memory**: Implements REST endpoints for managing `agent_memories` (global & map-scoped), enabling user sovereignty (edit, disable, delete), full GDPR JSON bundle export, and internal worker proposals with deduplication.
- **Data Persistence**: Direct asynchronous PostgreSQL access using `asyncpg` connection pools. Manages mind maps, nodes, edges, chat threads, version snapshots, agent memories, and background jobs.
- **Optimistic Concurrency Control (OCC)**: Map graph updates and restores validate `expected_version`, guaranteeing consistency across concurrent editors without silent overwrites.
- **Topological Integrity & Layout**: Enforces single-root DAG invariants, blocks self-loops, checks for cycles during edge modifications, prevents central root node deletion, and computes deterministic Top-to-Bottom tree coordinates with zero collisions.
- **Redis-Backed Distributed Rate Limiting**: Enforces sliding-window rate limits across multi-instance deployments for generation, chat, and memory proposals.
- **Dynamic Runtime Pipeline Configuration**: Hot-toggles pipeline flags (`ENABLE_TWO_PASS`, `RESEARCH_ENGINE`, `IMAGE_EXTRACTOR`, model selection) via `/admin/config` and serves them to workers via `/internal/config`.
- **Storage Management**: Handles binary uploads (PDF, DOCX, PPTX, images, text) into the private Supabase Storage bucket `seed-assets` under `<user_id>/<map_id>/...` and cleans them up transactionally on map deletion.
- **Queue Dispatcher**: Publishes `GenerationRequest` jobs to Upstash Redis for asynchronous execution by the detached ARQ worker service (`llm/`).
- **Internal Callbacks**: Exposes token-protected `/internal/jobs/{job_id}/...` endpoints for progress updates, node/edge draft persistence, and job completion from the worker.

---

## Directory Structure

```text
backend/
├── app/
│   ├── main.py               # Application entrypoint & middleware configuration
│   ├── auth/                 # Authentication services, schemas, and password hashing
│   ├── chat/                 # Conversational assistant router & action proposal execution
│   ├── core/                 # Config (settings), Supabase admin client, security helpers
│   ├── db/                   # Connection pool management and SQL repository queries
│   ├── contracts/            # Wire contracts (GenerationRequest, FileRef, JobType)
│   ├── maps/                 # Map CRUD, graph mutation, nodes/edges, OCC graph validator
│   ├── routes/               # API routers (auth, users, internal worker callbacks)
│   ├── services/             # ARQ queue dispatcher, worker runner, mindmap service
│   ├── snapshots/            # Map snapshot versioning, history, and restore handlers
│   └── uploads/              # Multipart file ingestion and Supabase Storage cleanup
├── db/
│   ├── schema.sql            # Master PostgreSQL schema definition (tables, RLS, indexes)
│   └── repository.py         # Asyncpg query repository for maps, nodes, edges, snapshots
├── tests/
│   └── unit/                 # Pytest unit tests (auth, contracts, storage cleanup)
└── pyproject.toml            # Dependencies, Ruff, Mypy, and Pytest configuration
```

---

## Key Endpoints (Sprint 3 Complete)

### Authentication & Profiles
| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/auth/signup`, `/auth/login`, `/auth/logout` | Session management with HttpOnly cookies & Bearer tokens. |
| `GET` | `/auth/google`, `/auth/google/callback` | Google OAuth with PKCE verifier cookie persistence. |
| `GET` | `/me` | Hydrate profile and role (`user` / `admin`) for authenticated session. |

### Mind Maps & Canvas Operations
| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/maps` | List user-owned mind maps with search and sorting. |
| `POST` | `/api/maps` | Create draft or manual mind map with optional `purpose` and `seed_url`. |
| `POST` | `/api/maps/{id}/generate` | Dispatch full-map generation job to worker. |
| `PATCH`| `/api/maps/{id}/graph` | Persist node positions & graph with `expected_version` OCC lock. |
| `POST` | `/api/maps/{id}/nodes` | Add concept node to map. |
| `PATCH`| `/api/maps/{id}/nodes/{node_id}` | Update node label/description. |
| `DELETE`| `/api/maps/{id}/nodes/{node_id}` | Delete node and clean up incident edges. |
| `POST` | `/api/maps/{id}/nodes/{node_id}/regenerate` | Dispatch partial branch regeneration. |
| `POST` | `/api/maps/{id}/edges` | Create edge connecting source and target nodes. |
| `PATCH`| `/api/maps/{id}/edges/{edge_id}` | Update edge label or route with cycle detection. |
| `DELETE`| `/api/maps/{id}/edges/{edge_id}` | Remove relationship edge. |
| `GET` | `/api/maps/{id}/snapshots` | List version snapshots with node/edge counts. |
| `POST` | `/api/maps/{id}/snapshots` | Create manual map snapshot. |
| `POST` | `/api/maps/{id}/snapshots/{snapshot_id}/restore` | Rollback map graph to previous version snapshot. |
| `GET` | `/api/maps/{id}/chat` | Fetch conversation history for map assistant. |
| `POST` | `/api/maps/{id}/chat` | Send query or refinement instruction to assistant. |
| `POST` | `/api/maps/{id}/chat/{message_id}/confirm` | Execute proposed graph delta modifications. |
| `POST` | `/api/maps/{id}/chat/{message_id}/dismiss` | Reject and dismiss proposed graph action. |

### Long-Term Agent Memory
| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/memory` | List user memories (optional `?map_id=...` filter, paginated). |
| `PATCH`| `/memory/{id}` | Edit memory content or toggle active/disabled state. |
| `DELETE`| `/memory/{id}` | Permanently delete memory row. |
| `GET` | `/memory/export` | Download full GDPR JSON export of user memories. |
| `POST` | `/internal/memory/propose` | Internal worker proposal endpoint (validation, dedup, decay). |
| `GET` | `/internal/memory/top` | Retrieve top-$k$ relevant memories for generation context. |

### Admin Oversight & Governance (`require_admin_role`)
| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/admin/users` | Paginated list of platform users (metadata only, zero user content). |
| `POST` | `/admin/users/{user_id}/suspend` | Suspend user account and log action to `admin_audit_log`. |
| `POST` | `/admin/users/{user_id}/unsuspend` | Restore user account access. |
| `DELETE`| `/admin/users/{user_id}` | Cascade-delete user account and clean up storage files. |
| `GET` | `/admin/stats` | Aggregate metrics (generation count, latency, failure rate). |
| `GET` | `/admin/audit-logs` | Chronological administrative audit logs with JSONB details. |
| `GET` | `/admin/config` | Read current active pipeline runtime settings. |
| `PATCH`| `/admin/config` | Update active pipeline settings (two-pass, research engine, model). |
| `GET` | `/internal/config` | Internal worker endpoint to fetch active pipeline settings. |

---

## Getting Started

### 1. Prerequisites
- Python 3.12+
- Access to Supabase PostgreSQL and Supabase Storage bucket (`seed-assets`)
- Upstash Redis instance (for background job queue)

### 2. Environment Configuration
Copy `.env.example` to `.env` in the `backend/` directory:
```bash
cp .env.example .env
```
Ensure the following variables are configured:
```ini
DATABASE_URL=postgresql://postgres:...@...pooler.supabase.com:6543/postgres
SUPABASE_URL=https://<your-project>.supabase.co
SUPABASE_ANON_KEY=eyJ...
SUPABASE_SERVICE_ROLE_KEY=eyJ...
REDIS_URL=rediss://default:...@...upstash.io:6379
JWT_SECRET=your-secure-jwt-secret
INTERNAL_API_SECRET=your-worker-callback-secret
```

### 3. Running the Server

Using `uv`:
```bash
uv sync
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Or using standard Python virtual environment:
```bash
python -m venv .venv
source .venv/bin/activate       # On Windows: .venv\Scripts\activate
pip install -e .
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

- API Base: `http://localhost:8000`
- Interactive OpenAPI Docs: `http://localhost:8000/docs`
- Health Check: `http://localhost:8000/api/health`

---

## Quality & Testing Commands

All quality gates must pass before submitting changes:

```bash
# 1. Format check
python -m ruff format --check app

# 2. Linter check
python -m ruff check app

# 3. Static type analysis
python -m mypy app

# 4. Unit test suite
python -m pytest tests -q
```
