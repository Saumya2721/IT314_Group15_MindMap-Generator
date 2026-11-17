# Mind Map Generator — LLM Worker

Decoupled asynchronous worker implementing **US-02** (seed normalization), **US-03** (generate mind map from seed), **US-04** (regenerate whole map), **US-06** (partial branch regeneration), and **US-07** (conversational chat assistant & action proposals) using **LangGraph**, **ARQ / Redis**, **Pinecone**, and a pluggable LLM/Decision Model architecture.

---

## Architecture Overview

The worker is completely decoupled from PostgreSQL and Supabase. The backend owns persistence, user authentication, and Supabase Row Level Security (RLS). The worker never touches PostgreSQL or user secrets directly.

```
[Frontend Client]
       │ Realtime polling on generation_jobs and chat messages
       ▼
[FastAPI Backend] ── owns Postgres, enforces JWT/RLS ─────────────────────────┐
       │ Enqueues GenerationRequest / ChatTurn to Redis                        │
       ▼                                                                       │
[ARQ Worker] (this codebase)                              [HTTP POST Callback] │
       │                                                  (Progress/Complete)  │
       ├─► 1. Pulls job from Redis Queue                                       │
       ├─► 2. Fetches file assets from signed Storage URLs                     │
       ├─► 3. Extracts text & token-budgets seed data (abstract-aware)         │
       ├─► 4. Embeds & upserts to Pinecone (per-map namespace)                 │
       ├─► 5. Executes LangGraph pipeline or Chat Turn state machine           │
       │      (Single-pass synthesis or two-pass branch expansion)             │
       └─► 6. Sends validated NodeDraft/EdgeDraft or Chat Answer ──────────────┘
```

---

## Sprint 2 Core Capabilities

### 1. Partial Node & Branch Regeneration (`JobType.REGENERATE_NODE`)
Allows users to select an existing concept on the canvas and regenerate only that subtree. The pipeline retrieves neighborhood context and branch-specific Pinecone embeddings, synthesizing fresh subtopics while preserving the parent connections.

### 2. Conversational Assistant & Action Proposals (`mindmap_llm.chat`)
- **Intent Classification**: Evaluates user chat queries to differentiate general inquiries/QA from graph modification instructions.
- **Topological QA (`chat/qa.py`)**: Answers questions with grounding citations and cited node IDs that highlight on the interactive canvas.
- **Action Proposals (`chat/agent.py`)**: Formulates structured delta proposals (`create_nodes`, `update_nodes`, `delete_nodes`, `create_edges`, `delete_edges`) returned to the frontend as actionable diff cards.

### 3. Dynamic Token Budgeting & LLM Hardening
- **Abstract-Aware Compaction (`format_seed_content`)**: Identifies titles, abstracts, and key section headings in long academic PDFs, compressing raw 12k+ token prompts into ~3.2k tokens to prevent HTTP 413 Payload Too Large errors.
- **Groq OTPM Clamping (`groq_provider.py`)**: Clamps requested output tokens to 900 for Qwen 27B on-demand models to respect Groq's 1,000 OTPM limit and prevent HTTP 429 Rate Limit Exceeded errors.
- **Edge Label Guarantee**: Automatically infers or defaults edge relationship labels to `"relates to"` if omitted, ensuring all edges render meaningful taxonomy badges.
- **Terminal Delivery Safety**: `tasks._send_terminal` wraps completion callbacks with error recovery so callback network blips never leave jobs stuck in `running` status in PostgreSQL.
- **Configurable Two-Pass Generation**: Full two-pass generation pipeline (Outline Generation → Parallel Branch Expansion → Graph Synthesis) available via `ENABLE_TWO_PASS=True`.

---

## LangGraph Pipeline & Progress Milestones

The pipeline runs through `orchestration.graph.run_generation`, reporting real-time progress via callback to the backend at 5 key milestones:

```
[Ingest] (10%) ──► [Routing] (20%) ──► [Retrieve] (40%) ──► [Generate] (70%) ──► [Validate] (85%)
                                                                  ▲                    │
                                                                  │                    ▼
                                                            (retry loop) ◄── [Validation Error]
                                                                                       │
                                                                                       ▼
                                                                           [Finalize] (100%)
```

1. **Ingest (10%)**: Seed text & files are chunked and embedded into Pinecone (skipped on regeneration to preserve the original seed).
2. **Classify Routing (20%)**: Decision model determines detail level (`overview`, `balanced`, `detailed`) and tool usage (`web_search`, `arxiv_search`).
3. **Retrieve (40%)**: Performs hybrid dense + sparse vector search in Pinecone and reranks candidates.
4. **Generate (70%)**: LLM synthesizes structured JSON node/edge mind map hierarchy.
5. **Validate (85%)**: Validates schema rules (DR-01 through DR-04: single root, acyclic reachability, unique IDs). Retries up to `MAX_GENERATION_RETRIES` on schema errors.
6. **Finalize (100%)**: Prepares wire contract drafts (`NodeDraft`, `EdgeDraft`) and reports completion.

---

## Role of Decision Model (Jev / System One)

The decision model is used exclusively for fast classification and reranking, never for generating mind map content:

| Stage | Call Site | Operation |
|---|---|---|
| Seed Intake | `seed.normalizer::_classify_is_supported_upload` | Classify valid uploads |
| Routing | `orchestration.nodes::classify_routing_node` | Detail level inference |
| Tool Invocation | `orchestration.nodes::classify_routing_node` | Research tool routing |
| Context Retrieval | `rag.retrieval::rerank_context` | Candidate chunk reranking |

**Graceful Degradation (NFR-07)**: Every decision model call routes through `SafeDecisionModel`. If Jev / TypeSafe is disabled, unreachable, or times out, it automatically falls back to the primary LLM, and finally to deterministic heuristics.

---

## Repository Structure

```text
llm/
├── mindmap_llm/
│   ├── callback/          # Internal HTTP callback client (progress, complete, fail)
│   ├── contracts/         # Wire contract models (GenerationRequest, GenerationResult, NodeDraft, EdgeDraft)
│   ├── embeddings/        # Pluggable embedding providers (HuggingFace, base interface)
│   ├── orchestration/     # LangGraph state machine, nodes, prompts, and dependency container
│   ├── providers/         # Primary LLM and decision model adapters (Groq, Jev, fallback chain)
│   ├── rag/               # Pinecone hybrid vector store (per-map namespace), chunking, retrieval
│   ├── scripts/           # Index management (create_pinecone_index.py) and local tester (run_local.py)
│   ├── seed/              # Document normalizers (PDF, text, OCR image parsing)
│   ├── tools/             # Research tools (Tavily web search, arXiv)
│   └── worker/            # ARQ worker settings, signed URL file fetcher, task entry point
├── tests/                 # Pytest test suite with zero external dependencies
└── pyproject.toml         # uv dependencies & tool configuration
```

---

## Setup & Running

### 1. Prerequisites
- Python 3.10+
- Redis 6+ (for ARQ job queue)
- Pinecone account & API key
- LLM Provider API key (e.g. Groq, OpenAI, or compatible)

### 2. Environment Configuration
Copy `.env.example` to `.env` and fill in your keys:
```bash
cp .env.example .env
```

Key environment variables:
- `REDIS_URL`: Connection string for Redis (e.g. `redis://localhost:6379`)
- `PINECONE_API_KEY`: Pinecone API key
- `PINECONE_INDEX`: Pinecone index name (default: `mindmap-chunks`)
- `EMBEDDING_MODEL` & `EMBEDDING_DIMENSION`: Default is `intfloat/e5-large-v2` (1024 dimension)
- `LLM_API_KEY`: Primary LLM API key

### 3. Initialize Pinecone Index
Run once before first deployment to create the serverless Pinecone index with the matching vector dimension:
```bash
python mindmap_llm/scripts/create_pinecone_index.py
```

### 4. Run the ARQ Worker
Start the worker listening for generation tasks:
```bash
arq mindmap_llm.worker.settings.WorkerSettings
```

### 5. Local Development Run (Without Redis)
To test mind map generation locally without needing an ARQ worker or backend server:
```bash
# Generate a new map
python mindmap_llm/scripts/run_local.py generate <map-uuid> --topic "Machine Learning" --ideas "Supervised, Unsupervised, RL"

# Full regeneration
python mindmap_llm/scripts/run_local.py regenerate <map-uuid> --instruction "Expand on reinforcement learning algorithms"
```

---

## Testing

Run the automated test suite:
```bash
pytest
```

All unit and integration tests run entirely in-process with **zero external dependencies**:
- LLM responses are simulated via `FakeLLMProvider`
- Decision calls are simulated via `FakeDecisionModel`
- Embeddings are generated via `FakeEmbeddingProvider`
- Pinecone namespaces and vector queries are simulated in-memory via `FakeVectorStore`
- Backend HTTP callbacks are captured and asserted via `FakeCallbackClient` and httpx mock transports

---

## Requirements Traceability

| Requirement | Implementation Component |
|---|---|
| **FR-04/05/06** (Seed capture, combining inputs) | `seed/normalizer.py`, `worker/files.py` |
| **FR-07/08/10** (Generate, tool augmentation, structured output) | `orchestration/`, `rag/`, `tools/` |
| **FR-09** (Regenerate entire map) | `orchestration/nodes.py::reconstruct_seed_from_namespace` |
| **FR-28/29** (Configurable providers & tools) | `providers/registry.py`, `embeddings/registry.py`, `tools/registry.py` |
| **DR-01..04** (Single root, graph connectivity, unique IDs) | `validation/schema.py` |
| **DR-05** (Preserve source meaning) | `orchestration/prompts.py` prompt constraints |
| **DR-07** (Multiple abstraction levels) | `detail_level` parameter & inference |
| **NFR-01/02** (Latency budgets) | `config.py::GenerationConfig` |
| **NFR-07** (Graceful error handling) | `providers/decision_fallback.py`, `worker/tasks.py` |
