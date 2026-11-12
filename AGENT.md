# Master AI Agent Guidelines (`AGENT.md`)

This is the definitive reference manual for any AI coding assistant modifying the **Mind Map Generator from Raw Ideas** codebase. Read and uphold these non-negotiable architectural invariants, security rules, and verification checklists before proposing or executing code changes.

---

## 1. Monorepo Architecture & Subsystem Boundaries

The codebase is organized into four strictly decoupled workspaces:

```text
IT314_Group15_MindMap-Generator/
├── backend/                  # FastAPI REST API, Supabase auth, Postgres asyncpg, storage
├── llm/                      # Detached ARQ background worker, LangGraph engine, Pinecone RAG
├── frontend/                 # React 18 SPA, Vite, @xyflow/react canvas, warm editorial UI
├── supabase/                 # PostgreSQL migrations, RLS policies, storage bucket config
├── docs/                     # Course specifications, requirements, and engineering rules
├── contracts/                # Shared wire contracts (symlinked/mirrored into backend & llm)
└── AGENT.md                  # This master agent instruction document
```

### System Boundaries
- **Backend owns persistence**: Only `backend/` connects to PostgreSQL and Supabase GoTrue Auth.
- **Worker is detached**: `llm/` receives jobs exclusively via Redis (`GenerationRequest`), processes text/multimodal assets, and reports back via authenticated HTTP callbacks (`/internal/jobs/{id}/...`). The worker **never** talks to PostgreSQL directly.
- **Frontend is a presentation client**: `frontend/` calls backend REST endpoints via `apiClient.js` proxies. For Sprint 1, the knowledge graph canvas is read-only.

---

## 2. Non-Negotiable Architectural & Security Invariants

### Invariant 1: Dual-Defense Authentication Protocol
- **Cookie + Header Hybrid**: The backend accepts both `Authorization: Bearer <token>` and `access_token` HttpOnly cookies.
- **No `localStorage` JWTs**: Frontend code must **never** write raw JWT tokens to `localStorage` (XSS vulnerability). Active session tokens are stored in active tab memory / `sessionStorage`, while long-term session persistence is anchored by HttpOnly cookies.
- **Silent Refresh**: On 401 Unauthorized, frontend automatically attempts `POST /auth/refresh` using the HttpOnly refresh token cookie before rejecting requests.

### Invariant 2: Database Schema & Supabase Storage Cleanup
- **Column Correctness**: The table `public.seed_assets` defines the column as **`storage_key`** (type `TEXT`). Column `storage_path` does **not** exist.
- **Transactional Cascade Safety**: `delete_map_storage_files` must clean up files in bucket `seed-assets` **before** executing `DELETE FROM public.mind_maps`.
- **Fail-Safe Abortion**: If Supabase Storage cleanup fails, `delete_map_storage_files` must raise an error and abort with `502 Bad Gateway`. The database row must never be deleted if storage cleanup fails, preventing orphaned binary objects.

### Invariant 3: Generation Job Lifecycle State Machine (`chk_generation_job_lifecycle`)
- PostgreSQL table `public.generation_jobs` enforces strict status transitions:
  - Jobs that have started (`started_at IS NOT NULL`) **cannot** have terminal status `rejected`. They must be set to `failed`.
  - Only jobs that never started (`started_at IS NULL`) can be set to `rejected`.
- Internal callback handlers in `backend/app/routes/internal.py` must strictly uphold this rule.

### Invariant 4: Topological DAG Integrity (DR-01 through DR-04)
- **Exactly One Root (DR-02)**: The synthesized mind map must have exactly one root node (in-degree = 0).
- **DAG Cross-Links Preserved**: Mind maps are directed acyclic graphs, **not** strict hierarchical trees. Cross-links (nodes with multiple parents) are deliberate and must never be pruned or collapsed.
- **Full Reachability (DR-03)**: Every non-root node must be reachable via directed edges originating from the central root node.
- **Unique IDs (DR-01)**: All node identifiers must be unique.

### Invariant 5: Regeneration Safety & Non-Destructive Cancellation (P0 Invariant)
- **Never Delete Existing Maps on Regeneration**:
  - Initial generation from scratch may clean up its empty placeholder draft if generation fails or is canceled.
  - Full map regeneration (`isRegeneration === true` or existing nodes count > 0) **must never** delete the mind map or its existing graph on failure or cancellation.
- **Polled Job Ref Guard**: Any job polled from `location.state.jobId` in `GeneratingPage.jsx` must be guarded by `polledJobIdRef` to prevent infinite polling loops on component re-renders.
- **Canonical Editor Redirect**: Upon job completion (`job.status === 'completed'`), the client must navigate to `/editor/${targetMapId}` with `{ replace: true, state: { mode: 'editor' } }` to ensure URL and view state are synchronized.

### Invariant 6: Open Redirect Protection on OAuth Callbacks
- OAuth return paths recorded in `sessionStorage` must be strictly sanitized in `OAuthCallbackPage.jsx`.
- Paths must begin with a single `/` and reject external or protocol-relative schemes (`//evil.com`, `https://`).

### Invariant 7: Sprint Scope Boundaries
- **Sprint 1 Scope (US-01 through US-04)**: Thought capture, seed upload, background worker synthesis, and read-only graph visualization.
- **Sprint 2 Roadmap (Explicitly Deferred)**: Canvas node dragging/coordinate persistence (`PUT /api/maps/{id}/graph`), inline node editing, node-level CRUD, undo/redo history, and custom vector exports. **Do not prematurely implement Sprint 2 editing features in Sprint 1 tasks.**

---

## 3. Monorepo Execution & Tooling Nuances

- **Directory Roots for Pytest**: Each Python workspace sets its own `pythonpath` in `pyproject.toml`. Pytest must always be executed from the workspace root:
  - Backend: `Set-Location "backend"; python -m pytest tests -q`
  - LLM: `Set-Location "llm"; python -m pytest tests -q`
- **Mypy Path Resolution**:
  - In `backend/pyproject.toml`: `mypy_path = ["app", "."]` is required because code imports `from core.config import settings`.
  - In `llm/pyproject.toml`: `mypy_path = "mindmap_llm"` is required.
- **FastAPI Idiom vs Ruff B008**:
  - FastAPI dependency injection (`= Depends(...)`, `= Form(...)`, `= File(...)`) triggers Ruff `B008` unless explicitly ignored. `B008` is ignored in `backend/pyproject.toml`.
- **Preserve Existing Comments & Docstrings**: Do not strip or alter unrelated comments, docstrings, or license headers.

---

## 4. Master Pre-Flight & Post-Flight Quality Matrix

Every agent task must verify that the corresponding quality gates pass with **0 errors** before concluding work:

### Backend Quality Suite
```powershell
Set-Location "backend"
python -m ruff format --check app   # Formatting check
python -m ruff check app            # Ruff linter (0 errors)
python -m mypy app                  # Mypy typecheck (0 errors across 24 source files)
python -m pytest tests -q           # Pytest unit tests (100% passing)
```

### LLM & Worker Quality Suite
```powershell
Set-Location "llm"
python -m ruff format --check mindmap_llm   # Formatting check
python -m ruff check mindmap_llm            # Ruff linter (0 errors)
python -m mypy mindmap_llm                  # Mypy typecheck (0 errors across 44 source files)
python -m pytest tests -q                   # Pytest unit tests (100% passing)
```

### Frontend Quality Suite
```powershell
Set-Location "frontend"
npm run format:check                # Prettier format check
npm run lint                        # ESLint (0 errors)
npm run test                        # Vitest unit test suite (100% passing)
npm run build                       # Vite production build (0 errors)
```

### Database & Migrations
```powershell
supabase test db                    # pgTAP schema and policy test suite
```
