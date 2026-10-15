# architecture.md — How the Mind Map Generator Backend Works

Comprehensive architectural guide to the FastAPI backend service: its core components, lifecycle state machine, queue orchestration, data models, error compensation, and integration with the LLM worker.

Companion documents:
- [`api.md`](../api.md): Complete unified API specification for frontend developers.
- [`llm/llm.md`](../llm/llm.md): Deep-dive into the LLM background worker and LangGraph pipeline.
- [`llm/api.md`](../llm/api.md): Low-level worker-backend contracts.

---

## 1. The Architecture in One Paragraph

The backend acts as the **central orchestrator, source of truth, security gatekeeper, and persistence engine** for the Mind Map Generator. Built on **FastAPI** and **Supabase (PostgreSQL + Storage + Auth)**, it authenticates users via JWTs or secure HTTP-only cookies, validates file uploads, enforces rate limiting, and manages mind map CRUD operations. Rather than generating mind maps in-process, it follows a **strict single-path architecture**: every generation and regeneration job is recorded in PostgreSQL in a `pending` state and enqueued to an **ARQ Redis queue**. An external asynchronous LLM worker consumes the job, downloads files from signed URLs, interacts with Pinecone and Groq LLMs, and invokes authenticated internal callback endpoints (`/progress`, `/complete`, `/fail`). Upon completion, the backend computes an automatic radial hierarchical visual layout (`(x, y)` coordinates) for the graph and commits the nodes and edges atomically inside a PostgreSQL transaction.

---

## 2. Backend Repository Layout

```
backend/
app/
├── main.py                     FastAPI application factory, lifespan startup, CORS, router mounting
├── core/
│   ├── config.py               Application settings via pydantic-settings (.env parsing)
│   ├── supabase.py             Supabase client, admin client, JWT validation, cookie auth
│   └── security.py             Security helpers, token generation, cryptographic hashing
├── db/
│   ├── schema.sql              Canonical PostgreSQL schema with constraints, triggers, and indices
│   ├── connection.py           asyncpg connection pool creator with JSONB/vector codecs & transaction helper
│   ├── models.py               Pydantic models representing DB entities (MindMap, Job, Node, Edge, etc.)
│   └── repository.py           High-performance asyncpg data access layer with strict transaction boundaries
├── maps/
│   └── routes.py               Core public REST API: /api/maps, /generate, /regenerate, /jobs
├── api/
│   ├── auth.py                 User authentication endpoints (/auth/signup, /auth/login, /auth/logout)
│   ├── internal.py             Worker callback endpoints (/internal/jobs/{id}/progress, /complete, /fail)
│   ├── maps.py                 Compatibility proxy forwarding to maps/routes.py
│   └── users.py                User profile management endpoints
├── services/
│   ├── generator.py            Auto-layout coordinate calculation (compute_layout)
│   ├── mindmap_service.py      High-level mind map business logic and transaction orchestration
│   ├── queue.py                ARQ Redis producer (enqueue_generation_job, get_redis_pool)
│   ├── runner.py               In-memory rate limiter (limiter)
│   └── storage.py              Signed URL generators and file management
├── uploads/
│   └── service.py              Magic-byte file sniffing, size validation, and Supabase Storage uploads
├── contracts/
│   └── models.py               Shared wire contracts with LLM worker (GenerationRequest, GenerationResult)
├── tests/
│   ├── test_generator_and_health.py
│   └── test_queue_contract.py  Contract tests for queue, callbacks, compensation, and 501 rejections
└── pytest.ini                  Pytest configuration (asyncio mode = auto)
```

---

## 3. End-to-End Request & Data Flows

### 3.1 Mind Map Generation Flow (`POST /api/maps/{map_id}/generate`)

```
[Frontend Client]
      │
      │ 1. POST /api/maps/{id}/generate (multipart: topic, ideas, files)
      ▼
[FastAPI Backend - maps/routes.py]
      │
      ├─► 2. Authentication & Authorization:
      │      Verifies user JWT/cookie via current_user_id.
      │      Executes assert_map_owner(pool, map_id, uid).
      │      Checks for active jobs via has_active_job(pool, map_id).
      │
      ├─► 3. Database Initialization (Transaction 1):
      │      Creates chat thread and user prompt message.
      │      Generates callback_token and stores sha256(callback_token) in DB.
      │      Inserts generation_jobs row with status='pending', progress=0.
      │
      ├─► 4. File Intake & Storage:
      │      Sniffs magic bytes (PDF, PNG, JPEG, WEBP) and validates <= 25MB.
      │      Uploads files to Supabase Storage: {uid}/{map_id}/{job_id}/{n}_{filename}.
      │      Generates short-lived signed URLs (15-30m expiry).
      │
      ├─► 5. ARQ Queue Dispatch:
      │      Assembles GenerationRequest payload.
      │      Enqueues task 'generate_mindmap' to Upstash/Redis.
      │      [If Redis down -> triggers storage cleanup + marks job 'rejected' -> HTTP 503].
      │
      └─► 6. Immediate Response:
             Returns HTTP 202 Accepted with {job_id, map_id, poll: "/api/maps/{id}/jobs/{job_id}"}.

[ARQ Redis Queue]
      │
      ▼
[LLM Background Worker]
      │
      ├─► 7. Download files from signed URLs, extract text, and chunk.
      ├─► 8. Upsert embeddings to Pinecone namespace=str(map_id).
      ├─► 9. LangGraph execution (Groq Llama 3.3 70B generates JSON nodes + edges).
      ├─► 10. POST /internal/jobs/{id}/progress (10% -> 20% -> 40% -> 70% -> 85%).
      │
      └─► 11. Terminal Callback:
             POST /internal/jobs/{id}/complete (with NodeDraft & EdgeDraft records)
             or POST /internal/jobs/{id}/fail (with GenerationError).

[FastAPI Backend - routes/internal.py]
      │
      ├─► 12. Validates Bearer token matches callback_token_hash.
      ├─► 13. State Conflict Check: Rejects if job already reached terminal state (HTTP 409).
      ├─► 14. Coordinate Layout: Computes visual tree coordinates (compute_layout).
      ├─► 15. Atomically Commits to PostgreSQL:
      │       - Inserts nodes with layout coordinates.
      │       - Inserts edges connecting nodes.
      │       - Appends assistant response to chat thread.
      │       - Updates generation_jobs: status='completed', progress=100, completed_at=now().
      ▼
[Frontend Polling / SSE]
      Detects status='completed' and renders the mind map canvas.
```

### 3.2 Full Map Regeneration Flow (`POST /api/maps/{map_id}/regenerate`)

1. User submits a high-level instruction (e.g., *"Reorganize into 4 main pillars with focus on scalability"*).
2. Backend verifies ownership and checks that the map has previously extracted seed assets (`has_seed`).
3. Backend records a new job with `job_type = 'regenerate_full'`, records the user prompt in the chat thread, and enqueues `GenerationRequest` to Redis.
4. The worker **does not require re-uploading files**: it reconstructs original seed text from the isolated Pinecone namespace (`namespace=str(map_id)`).
5. The LangGraph pipeline synthesizes a new mind map adhering to the new instruction while grounding factual claims in the original seed context.
6. On `/internal/jobs/{id}/complete`, the backend replaces previous nodes/edges atomically and updates the map.

### 3.3 Quick Generation Flow (`POST /api/maps/quick-generate`)

- Designed for rapid UI creation or quick CLI access without requiring multi-step uploads.
- Strictly adheres to the **single-path architecture**: generates a map ID and job ID, commits a `pending` job to PostgreSQL, and enqueues to Redis.
- Instead of returning 202 immediately, the endpoint asynchronously polls the database up to 60 seconds.
- If the worker completes generation within the window, it returns HTTP 200 with the full mind map graph.
- If generation exceeds 60 seconds, it gracefully returns HTTP 202 with the standard polling URL.

---

## 4. Database Architecture & Lifecycle State Machine

### 4.1 Strict Lifecycle State Machine (`chk_generation_job_lifecycle`)

The database enforces a strict lifecycle constraint on `public.generation_jobs` via PostgreSQL `CHECK`:

```sql
CONSTRAINT chk_generation_job_lifecycle CHECK (
    (status = 'pending'   AND started_at IS NULL     AND completed_at IS NULL     AND progress_percent = 0) OR
    (status = 'running'   AND started_at IS NOT NULL AND completed_at IS NULL) OR
    (status = 'completed' AND started_at IS NOT NULL AND completed_at IS NOT NULL AND progress_percent = 100) OR
    (status = 'failed'    AND started_at IS NOT NULL AND completed_at IS NOT NULL) OR
    (status = 'rejected'  AND started_at IS NULL     AND completed_at IS NOT NULL AND progress_percent = 0)
)
```

| State | `started_at` | `completed_at` | `progress_percent` | Meaning |
| :--- | :--- | :--- | :--- | :--- |
| `pending` | `NULL` | `NULL` | `0` | Enqueued in Redis; worker has not picked it up. |
| `running` | `NOT NULL` | `NULL` | `0 .. 99` | Worker has started processing; reporting progress. |
| `completed` | `NOT NULL` | `NOT NULL` | `100` | Successfully generated, laid out, and persisted. |
| `failed` | `NOT NULL` | `NOT NULL` | `0 .. 99` | Worker encountered unrecoverable engine/LLM error. |
| `rejected` | `NULL` | `NOT NULL` | `0` | Rejected before starting (queue down, empty seed, upload failure). |

### 4.2 Terminal Callback Conflict Protection (HTTP 409)

To prevent race conditions where a late or delayed worker callback overwrites a failure state:
1. When `/internal/jobs/{id}/complete` is received:
   - If DB job status is `failed` or `rejected`: **Rejects immediately with HTTP 409 Conflict**.
   - If DB job status is already `completed`: Returns idempotent HTTP 200 without duplicate insertion.
2. When `/internal/jobs/{id}/fail` is received:
   - If DB job status is already `completed`: **Rejects immediately with HTTP 409 Conflict**.
   - If DB job status is already `failed` or `rejected`: Returns idempotent HTTP 200.

### 4.3 Database Tables & Relationships

```
auth.users (Supabase Auth)
    │
    ├──◄ public.mind_maps (id, user_id, title, is_public, created_at, updated_at)
    │        │
    │        ├──◄ public.nodes (id, map_id, parent_id, label, description, node_type, pos_x, pos_y, color)
    │        │        │
    │        │        └──◄ public.edges (id, map_id, source_id, target_id, label, edge_type)
    │        │
    │        ├──◄ public.generation_jobs (id, map_id, job_type, status, progress_percent, callback_token_hash)
    │        │        │
    │        │        └──◄ public.seed_assets (id, map_id, generation_job_id, storage_path, content_text)
    │        │
    │        └──◄ public.chat_threads (id, map_id, user_id)
    │                 │
    │                 └──◄ public.chat_messages (id, thread_id, role, content, created_at)
```

### 4.4 Pinecone Vector Isolation Schema

Vector embeddings are managed in **Pinecone** with per-map namespace isolation (`namespace=str(map_id)`). The PostgreSQL schema retains a lightweight reference table:
```sql
CREATE TABLE public.node_embeddings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    node_id UUID NOT NULL REFERENCES public.nodes(id) ON DELETE CASCADE,
    pinecone_vector_id VARCHAR(100) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_embeddings_pinecone_vector_id ON public.node_embeddings(pinecone_vector_id);
```

---

## 5. Queue Failure Recovery & Compensating Transactions

When a user submits files and triggers generation, operations span multiple systems: PostgreSQL, Supabase Storage, and Redis. The backend implements **compensating transactions** to ensure resilience:

1. **Storage Cleanup on Partial Upload**:
   - If a batch of 3 files is uploaded, and the 2nd upload fails due to network interruption, `store_files` immediately calls `admin.storage.from_("seed-assets").remove([uploaded_key_1])` before raising HTTP 502.
2. **Storage Cleanup on Queue Decline**:
   - If files are uploaded to Storage successfully, but Redis connection fails or declines job enqueue:
     1. Compensating cleanup removes all `stored_keys` from Supabase Storage.
     2. Compensating DB transaction marks `generation_jobs`:
        ```sql
        UPDATE public.generation_jobs
           SET status = 'rejected', completed_at = now(), progress_percent = 0, error_message = $2
         WHERE id = $1 AND started_at IS NULL;
        ```
     3. Preserves `chk_generation_job_lifecycle` constraint (`rejected` requires `started_at IS NULL`).
     4. Raises HTTP 503 Service Unavailable with clear diagnostics.

---

## 6. Visual Layout Engine (`services/generator.py`)

Mind maps generated by the LLM contain purely semantic structure (`parent_id`, `node_type`). The backend layout engine calculates initial Cartesian coordinates `(pos_x, pos_y)` before inserting nodes into PostgreSQL:

```python
def compute_layout(nodes: list[dict], edges: list[dict]) -> list[dict]:
```

1. **Root Centering**: The single `root` node is placed at origin `(0, 0)`.
2. **Radial Angle Distribution**: Main topics (children of root) are evenly distributed along an ellipse/circle with radius $R_1 \approx 300\text{px}$.
3. **Branch Sector Fan-Out**: Subtopics fan outward radially in their parent's angular cone with incremental radius $R_2 \approx 500\text{px}$ and angular spread.
4. **Collision Avoidance**: Leaf nodes and deep branches are offset along the Y-axis to ensure no overlapping nodes.
5. **Client Drag Flexibility**: Frontend canvas libraries (e.g., React Flow) can freely update coordinates via `PATCH /api/maps/{map_id}/nodes/{node_id}` without constraint violations.

---

## 7. Security & Authentication Architecture

1. **Dual Authentication Support**:
   - **Bearer Token**: `Authorization: Bearer <access_token>` (for standard API clients and mobile).
   - **Secure Cookies**: Reads `sb-access-token` and `sb-refresh-token` with `HttpOnly`, `SameSite=Lax`, `Secure` flags.
2. **Cryptographic Callback Verification**:
   - Backend generates a high-entropy 256-bit random URL-safe secret (`secrets.token_urlsafe(32)`).
   - Backend stores only `sha256(callback_token)` in `generation_jobs.callback_token_hash`.
   - Worker transmits raw token in `Authorization: Bearer <token>`.
   - Backend hashes incoming token with SHA-256 and verifies equality via constant-time comparison.
3. **Row-Level Ownership Protection**:
   - Every mutation route executes `assert_map_owner(conn, map_id, uid)`. If the authenticated user does not own the map, queries immediately return HTTP 404 (preventing ID enumeration).

---

## 8. Running the Backend Locally

```bash
# 1. Activate backend virtual environment
cd backend
.\venv\Scripts\activate

# 2. Verify environment configuration (.env)
# Ensure SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, DATABASE_URL, and REDIS_URL are populated.

# 3. Run development server
uvicorn main:app --reload --port 8000

# 4. Run full test suite
pytest tests ..\llm\tests
```
