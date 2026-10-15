# llm.md — How the Mind Map Generator LLM Worker Works

Complete guide to this repository: what it does, how a generation request flows through it, and how to maintain and extend it.  
Companion doc: [`api.md`](./api.md) (how the backend interacts with this worker over Redis and HTTP callbacks).

Scope: **US-02** (seed normalization), **US-03** (mind map generation from seed), **US-04** (full map regeneration), plus supporting pieces of FR-04..10, FR-28/29/35/37/40 and DR-01..08.  
Not in this repo: User authentication (Supabase Auth), canvas frontend UI, export engine, and PostgreSQL persistence.

---

## 1. The Architecture in One Paragraph

The service operates as an **asynchronous worker**. It pulls a typed `GenerationRequest` from Redis via ARQ, downloads any user files from signed Storage URLs, normalizes and extracts the text, embeds chunks into **Pinecone** under an isolated namespace (`namespace=str(map_id)`), and executes a **LangGraph** orchestration graph. The graph routes the request, retrieves relevant chunks, asks a primary LLM (Groq `llama-3.3-70b-versatile` by default) for a strict JSON graph (`nodes` + `edges`), validates that graph against domain rules (single root, connected tree/DAG, unique IDs, no self-loops), and reports real-time progress. When generation completes or fails, it notifies the backend via an internal HTTP callback.

Two models play distinct roles:

| Role | Model Class | Used For | Never Used For |
| :--- | :--- | :--- | :--- |
| **Generator** | Primary LLM (Groq `llama-3.3-70b-versatile` by default) | Generating the node/edge JSON mind map | System routing and scoring |
| **Decision Model** | "System One" Model (Jev by TypeSafe AI), optional | Fast typed decisions: classify detail level, score retrieved context | Generating open-ended prose or node content |

If the decision model is unavailable or unconfigured, the generator LLM (and then a deterministic heuristic) takes over those tasks automatically (NFR-07: graceful degradation).

---

## 2. Repository Layout

```
config.py                  All settings loaded from environment variables (frozen dataclasses)
worker/
  settings.py              ARQ WorkerSettings (Redis connection, on_startup client initialization)
  tasks.py                 generate_mindmap(): ARQ entry point, error handling, terminal callbacks
  files.py                 fetch_all(): downloads raw file bytes from backend signed URLs
contracts/
  models.py                Typed Pydantic wire contracts: GenerationRequest, GenerationResult, NodeDraft, etc.
callback/
  client.py                JobCallbackClient: HTTP POST client for /progress, /complete, /fail
orchestration/
  graph.py                 LangGraph definition + retry loop + run_generation()
  nodes.py                 Pipeline node functions (ingest, route, retrieve, generate, validate)
  state.py                 GenerationState TypedDict flowing through the LangGraph nodes
  deps.py                  GenerationDeps: bundles LLM, decision model, vector store, tools, callback
  prompts.py               System/user prompt builders + tolerant JSON extraction
providers/
  base.py                  LLMProvider abstract interface (+ Message, ToolSpec, LLMResponse)
  groq_provider.py         Groq adapter (OpenAI-compatible chat completions)
  decision_model.py        DecisionModel interface (classify / score / health_check)
  jev_provider.py          Jev System One adapter (HTTP, short timeout)
  decision_fallback.py     LLMBackedDecisionModel and HeuristicDecisionModel fallbacks
  registry.py              Factory functions & SafeDecisionModel fallback cascade
embeddings/
  base.py                  EmbeddingProvider interface (dimension + model_name contract)
  huggingface_provider.py  Hugging Face Inference API adapter (intfloat/e5-large-v2, 1024 dims)
  registry.py              get_embedding_provider()
rag/
  pinecone_store.py        PineconeVectorStore: namespace-isolated hybrid (dense + BM25) search
  ingestion.py             chunk_text (paragraph-aware chunker), ingest_seed_text, ingest_tool_result
  retrieval.py             retrieve_context (hybrid query), rerank_context (decision model scoring)
seed/
  extractors.py            PDF extraction (pypdf), image OCR (pytesseract/PIL), text normalizer
  normalizer.py            normalize_seed: parses files/text into NormalizedSeed + SeedAssetDrafts
tools/
  base.py                  Tool interface, ToolResult, ToolError
  arxiv_tool.py            arXiv Atom API search
  web_search_tool.py       Tavily search API
  registry.py              build_tool_registry()
validation/
  schema.py                Pydantic draft models + domain rules (DR-01..08) + UUID assigner
scripts/
  create_pinecone_index.py One-time Pinecone index setup (sets metric to dotproduct)
  run_local.py             Local CLI runner (bypasses Redis for testing)
tests/                     Comprehensive unit & integration test suite (pytest)
```

**Design Rule**: Orchestration code never imports third-party vendor SDKs directly. All models, tools, vector stores, and callbacks are passed in as dependencies via `GenerationDeps`.

---

## 3. End-to-End Workflow

### 3.1 First Generation: `job_type = "generate"` (US-03)

```
[ARQ Worker]
     │
     ├─► 1. File Download (worker/files.py)
     │      Downloads signed URLs. If a file is corrupt/expired, it is logged to
     │      rejected_files and generation continues with the remainder.
     │
     ├─► 2. Seed Intake & Normalization (seed/normalizer.py)
     │      Extracts text from PDFs, images (OCR), and topic/ideas.
     │      Produces combined_text and SeedAssetDraft metadata.
     │      (If no content is usable, emits status='rejected' immediately).
     │
     ├─► 3. LangGraph Pipeline (orchestration/graph.py)
     │      ┌─────────────────────────────────────────────────────────────┐
     │      │ ingest node: chunks combined_text & upserts to Pinecone     │
     │      │              namespace=map_id. Progress -> 10%.             │
     │      │                                                             │
     │      │ classify_routing node: decision model infers detail level   │
     │      │              and selects research tools. Progress -> 20%.   │
     │      │                                                             │
     │      │ retrieve node: executes hybrid search in Pinecone and       │
     │      │              reranks top chunks. Progress -> 40%.           │
     │      │                                                             │
     │      │ generate node: LLM generates JSON {nodes, edges}.           │
     │      │              Progress -> 70%.                               │
     │      │                                                             │
     │      │ validate node: checks DR-01..DR-08. Progress -> 85%.        │
     │      │   ├─ Valid  -> finalize_success node                        │
     │      │   └─ Errors -> loops back to generate (max 2 retries)       │
     │      │                with validation feedback in prompt.          │
     │      └─────────────────────────────────────────────────────────────┘
     │
     └─► 4. Terminal Callback (callback/client.py)
            POST /internal/jobs/{id}/complete (with NodeDraft & EdgeDraft records)
            or POST /internal/jobs/{id}/fail (with GenerationError details).
```

### 3.2 Full Regeneration: `job_type = "regenerate_full"` (US-04)

1. The worker does not need the user to re-upload files or resend text.
2. It calls `reconstruct_seed_from_namespace(deps, namespace=str(map_id))`, which reads all `uploaded_seed` chunks previously stored in the Pinecone namespace.
3. The original seed text is combined with the user's `regeneration_instruction`.
4. The pipeline generates a fresh mind map adhering to the new instruction while maintaining the factual base context.
5. On completion, the backend replaces previous nodes/edges with the new graph.

---

## 4. LangGraph Nodes & State

The graph state is defined in [`orchestration/state.py`](./orchestration/state.py) as `GenerationState`:

```python
class GenerationState(TypedDict, total=False):
    job_id: UUID
    map_id: UUID
    namespace: str                          # Pinecone namespace (str(map_id))
    seed_text: str
    detail_level: str | None                # "overview" | "balanced" | "detailed" | None
    resolved_detail_level: str              # Set after classify_routing
    regeneration_instruction: str | None
    is_regeneration: bool

    candidate_draft: dict | None            # Raw JSON from LLM
    nodes: list[NodeDraft]                  # Validated nodes with real UUIDs
    edges: list[EdgeDraft]                  # Validated edges
    validation_errors: list[str]            # Feedback passed to LLM on retry
    retry_count: int                        # Incremented on each validation retry

    status: str                             # "completed" | "failed"
    error_message: str | None
    failed_stage: str | None
```

### Graph Router Logic (`_validate_router`)
- If validation succeeds: routes to `finalize_success` &rarr; `END`.
- If validation fails and `retry_count <= MAX_GENERATION_RETRIES`: routes back to `generate` with errors injected into the user prompt.
- If retries are exhausted: routes to `finalize_failure` &rarr; `END`.

---

## 5. Domain Rules (DR-01 through DR-08)

Enforced strictly in [`validation/schema.py`](./validation/schema.py):

* **DR-01 (Label & Field Limits)**: Node labels must not be empty and must be &le; 255 characters. Edge labels &le; 100 characters.
* **DR-02 (Single Root)**: The graph must contain **exactly one root node** (a node with in-degree 0).
* **DR-03 (Reachability & Connectedness)**: Every node must be reachable from the root. Disconnected islands or orphan nodes are rejected.
* **DR-04 (No Self-Loops & Valid References)**: No edge may point from a node to itself (`source != target`), and all edge endpoints must exist in the node list.
* **DR-05 (Acyclic / Hierarchy)**: The primary graph represents a tree or DAG hierarchy.
* **DR-06 (Colors)**: `background_color` and `text_color` must match `#RRGGBB` hex format if provided.
* **DR-07 (Provisional IDs)**: The LLM generates provisional strings (`"n1"`, `"n2"`). During validation, `resolve_to_records()` maps them deterministically to real UUIDs so internal IDs never leak to the database.
* **DR-08 (Zero Coordinate Assumption)**: The worker outputs nodes without positions (`pos_x=0`, `pos_y=0`). Layout computation is delegated to the frontend canvas (e.g. Dagre/ELK).

---

## 6. Vector Storage & Retrieval (Pinecone)

Vector storage is managed by [`rag/pinecone_store.py`](./rag/pinecone_store.py):

1. **Namespace Isolation**:
   Every query and upsert requires `namespace=str(map_id)`. Pinecone partitions vectors by namespace at the storage level.
2. **Hybrid Search (Dense + BM25)**:
   - Uses Pinecone's recommended hybrid query pattern combining dense embeddings and sparse BM25 vectors.
   - Scaled by `PINECONE_DENSE_WEIGHT` (convex combination $\alpha \cdot \text{dense} + (1-\alpha) \cdot \text{sparse}$).
   - If `pinecone-text` is not installed or fails initialization, the store gracefully falls back to dense-only vector search.
3. **Native Dimension Alignment**:
   - `EMBEDDING_MODEL=intfloat/e5-large-v2` produces native 1024-dimensional vectors.
   - The Pinecone index is created with `dimension=1024` and `metric="dotproduct"`.
   - **No random projection tricks**: vectors match the exact native model dimension.

---

## 7. How to Run and Test

### 1. One-Time Pinecone Index Creation
Before running the worker for the first time:
```powershell
python scripts/create_pinecone_index.py
```

### 2. Start the Production ARQ Worker
```powershell
arq worker.settings.WorkerSettings
```

### 3. Local Run Without Redis / Queue
To test the full LangGraph pipeline locally without running Redis or the backend:
```powershell
python scripts/run_local.py generate 00000000-0000-0000-0000-000000000001 --topic "Machine Learning" --ideas "Supervised, Unsupervised, Reinforcement"
```

### 4. Run the Pytest Test Suite
All 35 unit and integration tests are offline and fully mocked:
```powershell
pytest -v
```
