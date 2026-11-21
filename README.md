# Mind Map Generator from Raw Ideas (MindWeave)

Group 15, IT314 Software Engineering. Transform raw ideas and reference material (text, PDFs, images) into interactive, topological knowledge graphs using an asynchronous LangGraph/ARQ LLM pipeline, a FastAPI REST orchestrator, and a React (@xyflow/react) visual studio.

---

## Current Status — Sprint 2 Complete

Sprint 2 deliverables are fully implemented, audited, and verified across all layers:
- **Interactive Drafting Canvas**: Move nodes with Cartesian coordinate persistence, add nodes, reparent branches, create/edit labeled edges with cycle protection, and toggle between visual canvas and hierarchical outline views.
- **Node-Level Partial Regeneration**: Regenerate individual concepts and subtrees in-place without overwriting the rest of the mind map.
- **Snapshot & Version History**: Automated snapshot triggers, manual snapshot creation, visual graph preview, and one-click rollback with optimistic concurrency control.
- **Conversational Mind Map Assistant**: Integrated topological chat assistant with cited concepts, grounded sources, and executable action proposals (interactive confirm/dismiss diff cards).
- **Multi-Format Export Engine**: Export mind maps to high-resolution PNG (transparent, dark, light), paginated vector PDF (A4/Letter), and structured Markdown outlines.
- **LLM Pipeline Hardening**: Abstract-aware token budgeting (reducing PDF prompts from ~12k to ~3.2k tokens), Groq OTPM rate-limit clamping, safe callback error handling, and optional two-pass hierarchical branch expansion.
- **Authentication & OAuth**: Dual-defense HttpOnly cookie + tab-scoped session authentication, including Google OAuth with PKCE verifier persistence.

---

## Workspace Layout

| Folder | Purpose |
| --- | --- |
| `backend/` | FastAPI REST API, PostgreSQL (`asyncpg`), Supabase Auth & Storage, and job queue dispatching. |
| `llm/` | Detached ARQ background worker, LangGraph state machine, Pinecone vector store, and Groq/Llama/Qwen models. |
| `frontend/` | React 18 SPA (Vite), `@xyflow/react` interactive canvas, Outline view, and Warm Editorial Studio design system. |
| `supabase/` | PostgreSQL schema migrations, RLS security policies, and storage bucket configuration. |
| `docs/requirements/` | Requirements, user stories (US-01 through US-10), stakeholder analysis, and sprint tracking. |
| `docs/engineering/` | System architecture, testing strategies, and coding rules. |

---

## Quick Start

### Prerequisites
- Python 3.10+ (Python 3.12 recommended)
- Node.js 20+ (Node 22 recommended)
- Redis instance (e.g. Upstash Redis)
- Supabase project (PostgreSQL, GoTrue Auth, `seed-assets` Storage bucket)

### 1. Backend Service
```bash
cd backend
cp .env.example .env          # Fill credentials (DATABASE_URL, SUPABASE_*, REDIS_URL, etc.)
python -m venv .venv
# Activate: source .venv/bin/activate (Linux/macOS) or .venv\Scripts\activate (Windows)
pip install -e .
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```
API Base: `http://localhost:8000` | OpenAPI Docs: `http://localhost:8000/docs`

### 2. LLM / ARQ Background Worker
```bash
cd llm
cp .env.example .env          # Fill GROQ_API_KEY, PINECONE_*, REDIS_URL, etc.
python -m venv .venv
# Activate: source .venv/bin/activate (Linux/macOS) or .venv\Scripts\activate (Windows)
pip install -e .
python -m arq mindmap_llm.worker.tasks.WorkerSettings
```

### 3. Frontend Application
```bash
cd frontend
cp .env.example .env
npm install
npm run dev
```
Studio UI: `http://localhost:3000`

---

## Verification & Checks

All quality gates pass with zero errors:

```bash
# Frontend (Vitest unit tests, ESLint, Prettier, Vite production build)
cd frontend
npm run lint
npm run test
npm run build

# LLM Worker (Prompts, token budgeting, validation, graph structure)
cd llm
pytest -p no:asyncio tests/test_prompts.py tests/test_validation.py

# Backend (FastAPI routes, auth, maps, snapshots)
cd backend
pytest tests/
```

For requirements, user stories, and design decisions, see `docs/requirements/`. Read `CONTRIBUTING.md` before opening a pull request. `main` is protected: changes require approval and passing CI checks.
