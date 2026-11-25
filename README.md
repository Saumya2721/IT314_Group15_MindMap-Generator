# Mind Map Generator from Raw Ideas (MindWeave)

Group 15, IT314 Software Engineering. Transform raw ideas and reference material (text, PDFs, images) into interactive, topological knowledge graphs using an asynchronous LangGraph/ARQ LLM pipeline, a FastAPI REST orchestrator, and a React (@xyflow/react) visual studio.

---

## Current Status — Sprint 3 Complete

Sprint 3 deliverables are fully implemented, audited, and verified across all layers:
- **Role-Based Admin Oversight & Governance (`S3-DB1`, `S3-BE1`, `S3-BE2`, `S3-BE3`, `S3-FE1`)**:
  - Role-based authorization (`profiles.role` = `user` | `admin`) enforced by a non-bypassable `require_admin_role()` FastAPI guard.
  - Dedicated Admin Panel (`/admin/users`, `/admin/stats`, `/admin/audit-logs`) for user suspension/unsuspension, account deletion with storage cascades, and aggregate-only usage analytics (generation latency, failure rates, daily active users).
  - Absolute privacy invariant: Admins have **zero SELECT permissions** on user content tables (`mind_maps`, `nodes`, `edges`, `chat_messages`, `agent_memories`), verified by pgTAP tests.
  - Immutable audit logging (`admin_audit_log`) tracking all administrative actions with structured JSONB metadata.
- **Long-Term Agent Memory (`S3-DB2`, `S3-BE4`, `S3-L3`, `S3-FE2`)**:
  - Persistent memory store (`agent_memories`) capturing durable user preferences, domain facts, and map decisions across chat turns and generation jobs.
  - Automatic memory extraction node in LangGraph post-synthesis with importance decay and deduplication.
  - Dynamic top-$k$ memory retrieval injected into LLM prompts under a strict token budget ceiling ($\le 10\%$ of context window).
  - User sovereignty via `/memory` UI: global and map-scoped memory tabs, enable/disable toggles, deletion, and one-click GDPR JSON export (`GET /memory/export`).
- **LangSmith Observability & Quality-Gate Datasets (`S3-OBS`)**:
  - Full pipeline tracing via `@traceable` spans across mind map generation, branch regeneration, chat turns, and research agent execution.
  - Dual project configurations (`mindweave-dev`, `mindweave-prod`) with automated evaluation datasets tracking node counts, tree depth, schema validity, and latency.
  - Zero-overhead no-op fallback when tracing environment variables are absent.
- **Automatic Context & Dynamic Token Budgeting (`S3-L7`)**:
  - Intelligent `ContextBudgetManager` dynamically allocating prompt space across system rules, seed summaries, long-term memories, RAG chunks, and conversation history.
  - Provider-aware token ceiling enforcement (Groq 8k/6k input ceilings, Qwen OTPM clamp, Gemini Flash 32k/128k) completely preventing HTTP 413 (Payload Too Large) and HTTP 429 (Rate/Token quota exceeded).
  - Multi-stage priority pruning hierarchy: prunes low-relevance RAG passages first, then stale messages, then dense external tool payloads.
- **Multi-Source Ingestion & Tool Ecosystem (`S3-R1`, `S3-T1`, `S3-T2`, `S3-T3`, `S3-T4`, `S3-L6`)**:
  - Extractor registry mapping MIME types to specialized document parsers.
  - Cloud Vision Integration: Google Gemini Flash vision API as primary image extractor with graceful PyTesseract fallback.
  - Native document parsing for DOCX (`python-docx`) and PPTX (`python-pptx`) files, converting heading styles and slide titles into structured Markdown.
  - Live research tools: Wikipedia REST API for topic trees, SSRF-guarded Page Reader (HTML $\to$ clean Markdown), and Semantic Scholar / arXiv search when map purpose is `research`.
  - Purpose-driven intake: Three distinct generation modes (`General`, `Study`, `Research`) steering model tool policies and depth.
  - Direct URL seed intake (`POST /api/maps` with `seed_url`) featuring public IP allowlisting and SSRF protection.
- **Bounded Research Agent Subgraph (`S3-A2`, `S3-A4`)**:
  - Autonomous research loop with query planner, parallel tool execution, canonical URL/DOI deduplication, 1-hour Redis result caching, and 10s execution budget.
  - Degraded-run alerts surfaced to the user when external search fails.
- **Dynamic Pipeline Runtime Configuration (`S3-BE8`, `S3-FE7`, `S3-L2`)**:
  - Live administrative control at `/admin/config` allowing hot-toggling of `ENABLE_TWO_PASS`, `RESEARCH_ENGINE`, `IMAGE_EXTRACTOR`, and model selection without redeploying services.
  - Internal zero-auth endpoint (`/internal/config`) keeping worker database-free.
- **Top-to-Bottom (TB) Tree Layout**:
  - Deterministic vertical tree layout engine (`compute_layout`, `compute_incremental_layout`) with root at $y=80$, level step $\Delta y = 200$, parent centering over children spans, zero card collision, and `source-bottom` / `target-top` edge routing.
- **Redis-Backed Rate Limiting (`S3-BE5`, `S3-L1`)**:
  - Distributed sliding-window rate limiter on Redis enforcing per-user and per-endpoint quotas on generation, chat, and memory proposals across multi-replica deployments.

---

## Sprint 2 Delivered Capabilities

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
python -m arq mindmap_llm.worker.settings.WorkerSettings
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

### Deploying the API and worker together

Apply `supabase/migrations/20261123000000_runtime_contract_repairs.sql` through the normal migration workflow before deploying the updated API. The migration adds memory timestamps and regeneration version checks, preserves audit rows during account deletion, and records cleanup progress. If older active memories have duplicate normalized text, it keeps the highest-ranked copy active and retains the others disabled.

Set matching random `INTERNAL_SERVICE_KEY` values in the API and worker environments. Set a matching `PIPELINE_CONFIG_SERVICE_KEY` on both services if configuration reads use a separate credential. In production, set `ENVIRONMENT=production` on both services. Start the updated worker before the API so it can process account cleanup and callback retries. Keep Redis persistent so deferred callback deliveries survive a restart.

The Compose frontend serves the SPA and proxies API requests through its published port. Set `FRONTEND_URL` on the API to that public frontend origin for OAuth redirects. Configure `EXPORT_CONVERTER_URL` with a reachable container or external endpoint if using the server-side export converter.

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
