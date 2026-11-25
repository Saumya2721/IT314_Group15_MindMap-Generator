# LLM & Worker Agent Instructions (`llm/AGENT.md`)

This guide outlines rules, architectural constraints, and verification protocols that any AI coding assistant must review before making modifications to `llm/`.

---

## 1. Scope & System Boundaries

- **Decoupled Architecture**: The worker is completely isolated from PostgreSQL and Supabase credentials. It never connects directly to the application database.
- **Queue Consumer**: The entrypoint is `mindmap_llm.worker.tasks.generate_mindmap`, driven by ARQ listening on Upstash Redis.
- **Reporting via Callbacks**: All progress updates (0% to 100%) and terminal results (`complete` or `fail`) are reported back to the FastAPI backend via signed HTTP callbacks (`mindmap_llm.callback.client.CallbackClient`).

---

## 2. Non-Negotiable Invariants

### A. Topological Graph Integrity (DR-01 to DR-04)
- **Root Node Constraint (DR-02)**: A mind map must contain **exactly one** root node (in-degree = 0).
- **DAG with Cross-Links**: Mind maps are directed acyclic graphs. Cross-links (nodes with in-degree > 1) are intentional and must **never** be pruned or flattened into strict single-parent trees.
- **Reachability (DR-03)**: Every non-root node must be reachable via directed edges originating from the root.
- **Unique Node IDs (DR-01)**: All node IDs in `NodeDraft` must be distinct.
- **Strict Validation**: LangGraph validation node (`mindmap_llm.validation.schema.validate_structure`) enforces these rules. If validation fails, it triggers the retry loop up to `MAX_GENERATION_RETRIES`.

### B. Safe Decision Model Degradation (NFR-07)
- The decision model (Jev / System One) is used strictly for fast classification and candidate reranking, never for content generation.
- All decision calls must flow through `SafeDecisionModel`. If Jev / TypeSafe is unreachable, times out, or returns invalid data, it must gracefully degrade:
  1. Primary LLM (Groq / OpenAI compatible).
  2. Heuristic deterministic fallback (`HeuristicDecisionModel`).
- **Never crash generation due to a decision model failure.**

### C. Isolated Vector Storage (Pinecone Namespaces)
- RAG embeddings are isolated using per-map namespaces (`namespace = str(map_id)`).
- Chunks from one mind map must never contaminate queries from another.

### D. Terminal Callback Guarantee
- A job must **never** terminate without delivering a terminal status callback to the backend.
- `worker/tasks.py` contains a top-level exception catch-all (`except Exception as exc:`) that guarantees an HTTP `fail(result)` callback is dispatched with an error message and retryable flag. If callback delivery itself raises an error, it is caught to avoid leaving jobs stuck in PostgreSQL `running` state.

### E. Abstract-Aware Token Budgeting (Preventing HTTP 413)
- Long document uploads (PDFs, raw notes) must never be injected raw into generation prompts.
- All prompt seed injection must use `format_seed_content(seed_data)`, which detects document titles, extracts abstracts, extracts markdown/numbered headings, and extracts representative paragraphs under a strict ~3.5k token budget.

### F. Rate-Limit Clamping (Preventing HTTP 429)
- On-demand models such as Groq `qwen/qwen3.8-27b` enforce an output token limit of 1,000 tokens per minute (OTPM).
- Provider wrappers (`groq_provider.py`) must clamp `max_tokens` to 900 to ensure requests never fail rate limits. Chat QA generation (`chat/qa.py`) must limit expected output to 800 tokens.

### G. Edge Relationship Guarantee
- All synthesized or extracted edges must possess a descriptive relationship label.
- In `validation.schema.resolve_to_records`, any edge with an empty or null label must default to `"relates to"` to preserve semantic readability.

### H. LangSmith Tracing & Safe Instrumentation (S3-OBS)
- `configure_langsmith()` runs once at startup in `mindmap_llm.worker.settings`.
- When `LANGSMITH_TRACING=false` or credentials are missing, tracing must be a complete no-op (zero network overhead, never crash jobs).
- All major entry points (`generate_mindmap`, `regenerate_node_task`, `chat_turn_task`, `research_planner`) must be instrumented with `@trace_span`.

### I. Dynamic Context Budgeting & Pruning Hierarchy (S3-L7)
- Dynamic token allocation via `ContextBudgetManager` must scale to active provider constraints (e.g. Groq 8k/6k input ceiling, Qwen OTPM clamp, Gemini Flash 32k/128k).
- Pruning hierarchy when context overflows:
  1. Low-scoring RAG context chunks.
  2. Older chat history turns outside sliding window.
  3. Dense external search tool payloads.
  Core system prompts and seed focus must **never** be pruned.

### J. Vision Extractor Fallback Chain (S3-L6, S3-R1)
- Images must route through Google Gemini Flash (`gemini-3.8-flash`) by default.
- If Gemini Flash is unreachable, unconfigured, or returns empty text, the extractor must fall back cleanly to local `pytesseract`. Never fail image ingestion without attempting the fallback.

### K. Research Subgraph Bounds & Tool Policies (S3-A2, S3-L1)
- The research loop must be bounded: at most 2 research rounds and $\le 10\text{s}$ total execution budget.
- All external tool calls must enforce $\le 5\text{s}$ individual timeouts and query the 1-hour Redis result cache (`research:cache:<hash>`).
- If research fails or times out, the pipeline must continue in degraded mode, flagging `state["research_degraded"] = True`.

### L. Long-Term Memory Extraction & Prompt Budget (S3-L3)
- Post-turn memory proposals to `POST /internal/memory/propose` must deduplicate normalized text and calculate importance decay.
- Memory prompt injection must never exceed 10% of the active provider context budget. If memory retrieval fails or times out, synthesis proceeds safely without memories.

---

## 3. Environment & Execution Specifics

- **Working Directory**: Always set location to `llm/` before running tests, linters, or typecheckers:
  ```powershell
  Set-Location "llm"
  ```
- **Type Checking**: Mypy is configured with `mypy_path = "mindmap_llm"`.
- **Ruff Configuration**: `line-length = 120`. `B904` and `B905` are ignored to allow standard exception handling and untagged zip iterators.
- **Windows Pytest Async Execution**: When running pytest on Windows in the sandbox, invoke unit tests with `-p no:asyncio` (e.g. `python -m pytest -p no:asyncio tests/test_prompts.py tests/test_validation.py`) to prevent teardown hangs.

---

## 4. Verification Quality Checklist

Before finalizing any changes to `llm/`, execute all four quality gates:

```powershell
# 1. Format check
python -m ruff format --check mindmap_llm

# 2. Linter check (Must report 0 errors)
python -m ruff check mindmap_llm

# 3. Static Type Analysis (Must report 0 issues across 44 source files)
python -m mypy mindmap_llm

# 4. Unit Test Suite (Must report 100% passed across 35 test files)
python -m pytest tests -q
```

