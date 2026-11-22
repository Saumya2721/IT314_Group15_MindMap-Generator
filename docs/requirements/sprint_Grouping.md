# Sprint Grouping

## Sprint 1 – User Onboarding, Seed Capture and AI Mind Map Generation

### Goal

Set up the complete initial user flow, allowing users to create an account, provide the material from which a mind map will be generated, and receive an AI-generated structured mind map.

### User Stories

- **US-01 – Sign Up / Sign In**
  - Implement email/password registration and login.
  - Add Google sign-in.
  - Handle invalid credentials and existing accounts.
  - Protect user-specific map data.

- **US-02 – Submit a Raw Seed**
  - Allow users to upload PDFs/images and enter text.
  - Support text-only, file-only, and combined inputs.
  - Validate unsupported or corrupted files.
  - Convert accepted inputs into a common seed representation.

- **US-03 – Generate a Mind Map from a Seed**
  - Send the seed to the generation service.
  - Support research tools such as web search or arXiv where required.
  - Generate structured nodes and edges.
  - Validate the generated structure before displaying it.
  - Handle slow generation and generation failures.

- **US-04 – Regenerate the Whole Map**
  - Allow users to provide a new instruction for regeneration.
  - Keep the original seed available.
  - Generate a new version without immediately losing the previous map.
  - Handle failed regeneration and allow retry.

### Sprint Outcome

At the end of this sprint, a user should be able to create an account, sign in, provide raw material, generate an AI-based structured mind map, and regenerate the complete map when the initial result is not suitable.

---

## Sprint 2 – Mind Map Editing, Persistence and Export

### Goal

Give users control over the generated map and allow them to refine, save, find, reopen, and export their work.

### User Stories

- **US-05 – Direct-Manipulation Canvas Editing**
  - Drag and reposition nodes.
  - Create or modify node connections.
  - Support undo and redo.
  - Prevent invalid connections.
  - Persist canvas changes.

- **US-06 – Node-Level Comment & Partial Regeneration**
  - Select an individual node or branch.
  - Add a comment or instruction.
  - Regenerate only the selected branch.
  - Keep unrelated parts of the map unchanged.
  - Show which nodes were changed.
  - Preserve the existing branch if regeneration fails.

- **US-07 – Export a Mind Map**
  - Export maps as PDF or PNG.
  - Include the complete map when it is larger than the visible canvas.
  - Preserve the current canvas state in the exported file.
  - Handle export failures.

- **US-08 – Save and Find Past Mind Maps**
  - Save map metadata, nodes, relationships, and positions.
  - Search saved maps by title or topic.
  - Reopen maps in their last saved state.
  - Handle save failures.
  - Keep saved maps separated by user permissions.

### Sprint 2 Evolution & Additional Deliverables (Added Afterwards)

During the execution of Sprint 2, several key capabilities were added to significantly enhance the editing and refinement workflow beyond simple form-based comments:

- **Conversational Chat Assistant & Action Proposals (Added Afterwards):**
  - Implemented an interactive AI assistant panel attached to each map.
  - Allows users to chat naturally about the map and receive structured action proposals (`add_node`, `edit_node`, `delete_node`, `elaborate_node`).
  - Action proposals require explicit user confirmation or dismissal before modifying the live canvas, preventing unwanted mutations.
- **Short-Term Conversational Memory (Added Afterwards):**
  - Chat thread persistence per map (`chat_threads` and `chat_messages` tables).
  - Dynamic sliding context window to maintain recent conversational context without exceeding model token limits.
  - Token-bounded prompt formatting (`format_seed_content`, abstract-aware PDF excerpting) to prevent context window overflow (LLM-413).
- **Map Snapshots & Version History (Added Afterwards):**
  - Point-in-time graph state capture via dedicated `map_snapshots` table.
  - Version history drawer with node/edge counts, visual graph preview, and collision-guarded rollback/restore (`If-Match` version checking).
- **Manual Node & Edge Creation (Added Afterwards):**
  - Direct canvas insertion dialog for creating root, branch, and concept nodes manually.
  - Visual edge connection with bidirectional binding of relationship labels (`label` and `relationship_label`).
- **Hierarchical Outline View (Added Afterwards):**
  - Real-time collapsible tree/DAG view with search filtering, edge label badges, and direct selection synchronization with the canvas.
- **Dedicated Headless Export Service (S2-BE6 / Added Afterwards):**
  - Headless conversion microservice (`ExportConversionAdapter`) for server-side high-resolution PDF/PNG rasterization.
  - Automatic seamless fallback to client-side HTML5 Canvas export if the converter microservice is offline.

### Sprint Outcome

At the end of this sprint, a user can manually edit the mind map, converse with an AI assistant to propose structural refinements, inspect version history and restore snapshots, search and reopen saved maps, navigate a hierarchical outline, and export maps to PDF, PNG, or SVG with client-side fallback.

---

## Sprint 3 – Platform Administration, Developer Configuration and QA Enablement

### Goal

Harden platform administration with role-based access control, deliver long-term memory and personalization for the assistant, establish dynamic context and token budgeting, instrument the pipeline with LangSmith observability, expand source intake with multimodal and academic research tools, and provide developers with a configurable, testable generation pipeline.

### Original Scope vs. Final Agreed Adjustments

> [!IMPORTANT]
> **Decisions on Scope Adjustments for Sprint 3:**
> - ❌ **Multi-User Collaboration & Real-Time Sync (FR-20 to FR-25) — Descoping / Not Implemented:**
>   *Because of high architectural complexity, real-time synchronization overhead, state reconciliation challenges, and strict sprint time constraints, it was decided not to implement multi-user collaboration in Sprint 3. The platform strictly maintains single-user per map ownership secured by PostgreSQL Row Level Security (RLS).*
> - ❌ **Server-Sent Events (SSE) for Progress (S3-BE6 / S3-FE6) — Not Implemented:**
>   *Discarded in favor of the existing smooth, robust 1-second polling loop, freeing backend and frontend bandwidth for core functional deliverables.*
> - ❌ **Testing Platform Formalities (QA1/QA2 full matrix) — Replaced:**
>   *Formal external CI matrix was replaced with deterministic in-repo mocks, fixtures (`S3-L5`), and pgTAP database security tests.*
> - ❌ **Agent Folder Separation Refactor (S3-A1) — Deferred:**
>   *Structural folder re-organization was deferred to the post-sprint backlog to prevent sprint-time merge conflicts; functional upgrades were implemented in-place.*

### User Stories & Feature Deliverables (Agreed on GitHub)

- **US-09 – Admin Oversight & Role-Based Access Control (S3-DB1, S3-BE1, S3-BE2, S3-BE3, S3-FE1)**
  - Implement role-based authentication (`profiles.role` column: `user` or `admin`).
  - Route-guarded Admin Panel (`/admin/*`) visible only to administrators.
  - User management API: paginated user listing, suspend/unsuspend, and account deletion.
  - Aggregate platform analytics API: total users, maps generated, job failure rates, average generation latency (reading strictly from aggregate views).
  - Administrative audit logging (`admin_audit_log` table) recording every administrative action.
  - **Zero SELECT privacy rule:** Database RLS and views guarantee that administrators have **no SELECT access** to private user content (`mind_maps`, `nodes`, `edges`, `chat_messages`, `agent_memories`).

- **US-10 – Configure the Generation Pipeline (S3-BE8, S3-FE7, S3-L2)**
  - Pipeline configuration API (`GET /admin/config`, `PATCH /admin/config`) for administrators.
  - Internal endpoint (`GET /internal/config`) enabling the LLM worker to fetch active flags without database access.
  - Admin UI settings page for toggling `ENABLE_TWO_PASS`, `RESEARCH_ENGINE`, `IMAGE_EXTRACTOR`, and viewing active prompt versions.
  - Versioned prompt templates and model selection with load-time validation.

- **US-11 – Test in Isolation & Deterministic Fixtures (S3-L5)**
  - Deterministic fake LLM provider and mock research tools for offline, hermetic CI runs.
  - Version-check concurrency guards to ensure regeneration operations never overwrite concurrent edits.
  - Automated pgTAP tests in `supabase/tests/database/` proving RLS isolation and the admin zero-SELECT privacy boundary.

- **US-12 – Long-Term Agent Memory & Personalization (Added Afterwards — S3-DB2, S3-BE4, S3-L3, S3-FE2)**
  - Persistent `agent_memories` table storing user preferences, decisions, and factual knowledge across sessions.
  - Asynchronous memory extraction node proposing durable memories to the backend after chat turns and map generations.
  - Token-budgeted memory retrieval injecting top-k relevant memories into prompt assembly (capped at ≤ 10% of context window).
  - Dedicated user memory management UI (`/memory`) with global/map-scoped filtering, enable/disable toggles, deletion, and GDPR JSON export.
  - RLS protection ensuring users only see their own memories and administrators see none.

- **US-13 – LangSmith Observability & Pipeline Tracing (Added Afterwards — S3-OBS)**
  - Full pipeline tracing via `@traceable` instrumentation across generation tasks, chat turns, branch expansions, and research planner nodes.
  - Dedicated LangSmith tracing projects (`mindweave-dev` and `mindweave-prod`).
  - Automated evaluation datasets integrated with Sprint 2 seed benchmarks (node count, depth, schema validity, latency).
  - Graceful degradation: tracing runs as a no-op when `LANGCHAIN_TRACING_V2` is absent, keeping CI clean without external API keys.

- **US-14 – Automatic Context & Dynamic Token Budgeting (Added Afterwards — S3-L7)**
  - Intelligent `ContextBudgetManager` dynamically allocating the context window across system prompt, seed excerpt, long-term memory, RAG passages, and chat turns.
  - Provider-aware token clamps (Groq 8k/6k ceilings, Qwen OTPM limits, Gemini Flash 32k/128k) preventing HTTP 413 and HTTP 429 rate/quota errors.
  - Hierarchical pruning order: lowest-scoring RAG chunks → older chat messages → dense external tool payloads.

- **US-15 – Multimodal Seed Intake & Expanded Document Formats (Added Afterwards — S3-BE7, S3-FE4, S3-FE5, S3-DB3, S3-T4)**
  - Public URL seed intake with SSRF protection (loopback/private IP blocklist and redirect caps).
  - Document ingestion expanded to DOCX and PPTX files with heading and slide title extraction.
  - Map generation purpose intake selector on frontend (`general`, `study`, `research`) driving backend tool routing.

- **US-16 – Advanced Research Tools & Subgraph (Added Afterwards — S3-A2, S3-A3, S3-A4, S3-T1, S3-T2, S3-T3)**
  - Bounded research agent subgraph (query planner → parallel tool execution → deduplication → Redis caching → sufficiency check).
  - Wikipedia research tool with CC BY-SA attribution linking.
  - Clean web page reader tool with SSRF guards.
  - Academic scholar tool (Semantic Scholar / OpenAlex) activated for research-purpose maps.
  - Shared `expand_branch` function unifying prompt building, quality gates, and source tagging across generation, regeneration, and chat elaboration.

- **US-17 – Vision Model for Image Ingestion (Added Afterwards — S3-L6, Deferred from Sprint 2 L-1)**
  - Google Gemini Flash vision model integration as the primary image-to-text extractor for uploaded image seeds.
  - Graceful fallback to Tesseract OCR when vision API is unavailable or returns empty text.

### Sprint Outcome

At the end of this sprint, the platform features robust role-based administration with audited actions and guaranteed privacy boundaries. The LLM assistant possesses long-term memory across sessions and maps, operates within dynamic token budgets that eliminate rate-limit failures, and is fully observable through LangSmith tracing. Seed intake accepts URLs, DOCX, PPTX, and images via Gemini Flash, and generates deep mind maps supported by Wikipedia, Web Page, and Scholar research tools.

---

# Notes

- **US-01 to US-04** were grouped into the first sprint because authentication and seed submission are the starting points of the main user flow, while mind map generation and whole-map regeneration directly depend on the seed being available.
- **US-05 to US-08** remained together in the second sprint because they all operate on the generated mind map. In addition, conversational refinement via an AI chat assistant, short-term conversational memory, point-in-time snapshots with safe version rollback, and outline navigation were added afterwards during Sprint 2 to complete the user editing experience.
- **US-09 to US-11 (and additions US-12 to US-17)** were grouped into the third sprint to deliver administrative security, long-term personalization, deep observability, resilient token budgeting, and multimodal research intake.
- **Multi-user collaboration & real-time sync (FR-20 to FR-25)**: Because of high architectural complexity, concurrency state synchronization, and sprint time constraints, multi-user collaboration was officially excluded from Sprint 3 implementation. Single-user per map security isolation was maintained via PostgreSQL Row Level Security.
