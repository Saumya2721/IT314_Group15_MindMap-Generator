# Mind Map Generator — Backend API

FastAPI service powering the Mind Map Generator. Handles user authentication, database persistence via PostgreSQL / Supabase, seed file uploads via Supabase Storage, and generation job dispatching via Upstash Redis / ARQ.

---

## Architecture & Responsibilities

The backend acts as the central orchestrator and security boundary for the system:
- **Authentication**: Dual-Defense architecture combining secure `HttpOnly` session cookies with in-memory Bearer token fallback. Integrated with Supabase GoTrue.
- **Data Persistence**: Direct asynchronous PostgreSQL access using `asyncpg` connection pools. Manages mind maps, nodes, edges, chat threads, and job tracking.
- **Storage Management**: Handles binary uploads (PDF, images, text) into the private Supabase Storage bucket `seed-assets` under `<user_id>/<map_id>/...` and cleans them up transactionally on map deletion.
- **Queue Dispatcher**: Publishes `GenerationRequest` jobs to Upstash Redis for asynchronous execution by the detached ARQ worker service (`llm/`).
- **Internal Callbacks**: Exposes token-protected `/internal/jobs/{job_id}/...` endpoints for progress updates, node/edge draft persistence, and job completion from the worker.

---

## Directory Structure

```text
backend/
├── app/
│   ├── main.py               # Application entrypoint & middleware configuration
│   ├── auth/                 # Authentication services, schemas, and password hashing
│   ├── core/                 # Config (settings), Supabase admin client, security helpers
│   ├── db/                   # Connection pool management and SQL repository queries
│   ├── contracts/            # Wire contracts (GenerationRequest, FileRef, JobType)
│   ├── maps/                 # Map CRUD and generation trigger endpoints (/api/maps)
│   ├── routes/               # API routers (auth, users, internal worker callbacks)
│   ├── services/             # ARQ queue dispatcher, worker runner, mindmap service
│   └── uploads/              # Multipart file ingestion and Supabase Storage cleanup
├── db/
│   └── schema.sql            # Master PostgreSQL schema definition (tables, RLS, indexes)
├── tests/
│   └── unit/                 # Pytest unit tests (auth, contracts, storage cleanup)
└── pyproject.toml            # Dependencies, Ruff, Mypy, and Pytest configuration
```

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
