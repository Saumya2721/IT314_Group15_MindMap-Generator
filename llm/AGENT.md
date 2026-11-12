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
- `worker/tasks.py` contains a top-level exception catch-all (`except Exception as exc:`) that guarantees an HTTP `fail(result)` callback is dispatched with an error message and retryable flag.

---

## 3. Environment & Execution Specifics

- **Working Directory**: Always set location to `llm/` before running tests, linters, or typecheckers:
  ```powershell
  Set-Location "llm"
  ```
- **Type Checking**: Mypy is configured with `mypy_path = "mindmap_llm"`.
- **Ruff Configuration**: `line-length = 120`. `B904` and `B905` are ignored to allow standard exception handling and untagged zip iterators.

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

