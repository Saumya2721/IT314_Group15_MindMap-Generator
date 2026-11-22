# Product Backlog --- User Stories

# Epic 1 --- Onboarding & Seed Capture

## US-01 --- Sign Up / Sign In

**Epic:** Epic 1 --- Onboarding & Seed Capture

### Front of the Card

**User Story**

> As a new User, I want to sign up or sign in directly with Google, so
> that I can securely access my mind maps without having to manage a
> separate account and password.

### Acceptance Criteria

#### Scenario 1 --- Email Sign-Up

**Given** a new User is on the sign-up page\
**When** they submit a valid email address and password\
**Then** an account is created\
**And** they are taken to the minimal main interface.

#### Scenario 2 --- Google Sign-In

**Given** a User does not have an existing account\
**When** they choose **Sign in with Google**\
**Then** the system authenticates them using Google OAuth\
**And** creates or logs into the corresponding account without requiring
a separate application password.

#### Scenario 3 --- Invalid Credentials

**Given** a User enters invalid or incomplete credentials\
**When** they submit the form\
**Then** the system rejects the request\
**And** displays a clear, non-sensitive error message.

#### Scenario 4 --- Existing Account

**Given** a User already has an account\
**When** they sign in successfully\
**Then** they are taken to their existing workspace and saved maps
remain accessible.

### Back of the Card

**Technical Notes**

-   Implements FR-01, FR-02, and FR-03.
-   Google authentication relies on a third-party OAuth provider.
-   Passwords must never be stored in plaintext.
-   Authentication/session tokens should be handled securely and should
    expire according to the application's security policy.
-   Account creation and login should use the same canonical User
    identity so that a User does not accidentally receive duplicate
    accounts.
-   Authentication errors should avoid revealing whether a particular
    email address is registered.
-   Supports NFR-04 (Security) and NFR-10 (Compatibility/third-party
    availability).

**Validation / Test Strategy**

-   Unit-test invalid credential rejection.
-   Test valid email/password registration and login.
-   Test duplicate-account handling.
-   Mock Google OAuth for automated tests.
-   Test successful Google OAuth login and account creation.
-   Test OAuth cancellation, provider failure, and expired/invalid
    authentication responses.
-   Verify that unauthenticated Users cannot access protected map data.

**Dependencies / Risks**

-   Google OAuth availability is a third-party dependency.
-   Authentication must degrade gracefully if the provider is
    temporarily unavailable, following the FR-37/NFR-10 pattern.
-   Account/session security is a high-impact area and should be tested
    before production release.

------------------------------------------------------------------------

## US-02 --- Submit a Raw Seed

**Epic:** Epic 1 --- Onboarding & Seed Capture

### Front of the Card

**User Story**

> As a User, I want to upload reference material such as images/PDFs
> and/or type a topic and free-text ideas, so that the system can build
> a mind map from raw material without requiring me to organize my
> thoughts first.

### Acceptance Criteria

#### Scenario 1 --- Upload + Text

**Given** the main interface\
**When** I upload a PDF and add free-text notes\
**Then** both inputs are combined into a single seed\
**And** the seed is ready for generation.

#### Scenario 2 --- Text-Only Seed

**Given** no files are uploaded\
**When** I enter only a topic and some ideas\
**Then** the system accepts the free text as a valid seed.

#### Scenario 3 --- File-Only Seed

**Given** a supported reference file is uploaded\
**When** I submit the seed without additional text\
**Then** the system accepts the uploaded material as the seed, provided
the file contains usable content.

#### Scenario 4 --- Invalid Upload

**Given** a malformed, corrupt, unsupported, or unreadable file\
**When** I attempt to submit it\
**Then** the system reports the problem clearly\
**And** does not block the User from continuing with valid text or other
supported inputs.

### Back of the Card

**Technical Notes**

-   Implements FR-04, FR-05, and FR-06.
-   Parsing should remain distinct for each input type according to
    DR-02.
-   The system should normalize all accepted inputs into a common
    **Seed** representation before generation.
-   A seed should retain enough metadata to identify its source
    material, such as text input, image, or PDF.
-   File-size and file-type limits should be enforced before expensive
    processing begins.
-   Uploaded content should be isolated and validated before being
    passed to downstream services.

**Validation / Test Strategy**

-   Test image, PDF, text-only, and combined-input seeds.
-   Verify that multiple input sources are merged into one valid seed
    object.
-   Test empty input and unsupported file types.
-   Test malformed/corrupt uploads.
-   Test maximum file size and unusually large text inputs.
-   Verify that extracted text is not silently lost during
    normalization.

**Dependencies / Risks**

-   PDF/image parsing libraries or services may fail on unusual files.
-   Large uploads can increase processing time and storage requirements.
-   User-uploaded content may contain sensitive information and must be
    handled according to the application's security/privacy
    requirements.

------------------------------------------------------------------------

# Epic 2 --- AI-Driven Mind Map Generation

## US-03 --- Generate a Mind Map from a Seed

**Epic:** Epic 2 --- AI-Driven Mind Map Generation

### Front of the Card

**User Story**

> As a User, I want to submit my seed and have the system generate a
> structured mind map, so that I receive a navigable starting structure
> without having to perform the research and organization myself.

### Acceptance Criteria

#### Scenario 1 --- Tool-Augmented Generation

**Given** a submitted seed\
**When** generation runs\
**Then** the LLM engine may call web search and/or arXiv search to
ground the result\
**And** returns structured nodes and edges.

#### Scenario 2 --- Slow Generation Feedback

**Given** generation takes longer than a few seconds\
**When** the request is still running\
**Then** the User sees a progress/status indicator rather than a frozen
screen.

#### Scenario 3 --- Successful Structured Output

**Given** the generation service returns a response\
**When** the response is processed\
**Then** the system validates the node and edge structure before
displaying it on the canvas.

#### Scenario 4 --- Generation Failure

**Given** the LLM or an external research tool fails\
**When** generation cannot complete normally\
**Then** the User receives a clear error/status message\
**And** the original seed remains available for retry.

### Back of the Card

**Technical Notes**

-   Implements FR-07, FR-08, FR-10, FR-35, and FR-40.
-   Depends on the LLM Service and relevant third-party APIs.
-   The generation layer should produce a predictable structured
    representation, e.g.:
    -   Node identifier
    -   Node title/content
    -   Parent/child or relationship information
    -   Position/layout information where applicable
    -   Optional source/reference metadata
-   Validate LLM output before it reaches the canvas.
-   Tool usage should be isolated behind a tool interface so additional
    research sources can be introduced later.
-   Generation should have timeout and retry behaviour.

**Validation / Test Strategy**

-   Mock LLM and tool responses according to NFR-08.
-   Test valid node/edge output parsing.
-   Test malformed, incomplete, duplicated, and unexpected LLM output.
-   Test tool timeout and API failure.
-   Verify that a slow request displays progress rather than appearing
    frozen.
-   Verify that generated nodes form a valid graph structure.

**Dependencies / Risks**

-   Tool-call latency and availability are subject to rate limits and
    external service availability.
-   LLM output is probabilistic and therefore requires schema
    validation.
-   Generation cost can increase with larger seeds, repeated tool calls,
    and repeated regeneration.
-   Research results should be distinguishable from model-generated
    structure where source attribution is required.

------------------------------------------------------------------------

## US-04 --- Regenerate the Whole Map

**Epic:** Epic 2 --- AI-Driven Mind Map Generation

### Front of the Card

**User Story**

> As a User, I want to re-prompt the system to regenerate or adjust the
> entire map, so that I can steer the result in a new direction if the
> first pass is not right.

### Acceptance Criteria

#### Scenario --- Full Regeneration

**Given** an existing generated map\
**When** I submit a new instruction and choose **Regenerate**\
**Then** a new map is produced using the original seed plus my new
instruction.

#### Scenario --- Preserve Original Seed

**Given** I regenerate an existing map\
**When** the regeneration request is created\
**Then** the original seed remains available as the base context\
**And** the new instruction is applied as additional guidance.

#### Scenario --- Failed Regeneration

**Given** regeneration fails\
**When** the request returns an error\
**Then** the previous map remains available\
**And** the User can retry without losing their existing work.

### Back of the Card

**Technical Notes**

-   Implements FR-09.
-   Preserve the original seed separately from the generated map.
-   Treat regeneration as a new generation version rather than
    immediately destroying the previous result.
-   Consider retaining generation history so that a User can recover a
    previous version.
-   The new instruction should be clearly separated from the original
    seed when constructing the generation request.

**Validation / Test Strategy**

-   Verify that the original seed is preserved and reused.
-   Verify that the new instruction affects the generation request.
-   Confirm that the previous map remains intact if regeneration fails.
-   Test repeated regeneration requests.
-   Test cancellation or timeout where supported.

**Dependencies / Risks**

-   Repeated full regenerations increase LLM cost and API usage.
-   Rate limits may restrict repeated generation.
-   Without versioning, Users may accidentally lose a useful previous
    map.

------------------------------------------------------------------------

# Epic 3 --- Interactive Canvas Editing

## US-05 --- Direct-Manipulation Canvas Editing

**Epic:** Epic 3 --- Interactive Canvas Editing

### Front of the Card

**User Story**

> As a User, I want to drag, reposition, and manually restructure nodes
> on the canvas, so that I can refine the map's layout and structure
> myself, similar to working in Figma.

### Acceptance Criteria

#### Scenario --- Reposition & Reconnect

**Given** a generated map is displayed on the canvas\
**When** I drag a node to a new position or connect it to a different
node\
**Then** the change is reflected immediately\
**And** the updated structure/layout persists.

#### Scenario --- Undo / Redo

**Given** I have made one or more canvas changes\
**When** I choose Undo or Redo\
**Then** the canvas returns to the corresponding previous or next state.

#### Scenario --- Invalid Connection

**Given** a connection would violate the map's structural rules\
**When** I attempt to create it\
**Then** the system prevents the invalid operation or clearly asks me to
correct it.

### Back of the Card

**Technical Notes**

-   Implements FR-11, FR-12, FR-13, and FR-16.
-   Canvas state should distinguish between:
    -   Node content
    -   Node position
    -   Node relationships
    -   Selection/UI state
-   Changes should be persisted without requiring a full map
    regeneration.
-   Undo/redo should operate on meaningful user actions rather than
    individual low-level mouse events.
-   The data model should remain compatible with future real-time
    future multi-user features.

**Validation / Test Strategy**

-   UI interaction tests for drag and drop.
-   Test node creation, deletion, reconnection, and repositioning where
    supported.
-   Test undo/redo across multiple actions.
-   Reload the map and verify persisted positions and relationships.
-   Test invalid or circular relationships if the domain prohibits them.

**Dependencies / Risks**

-   Canvas state will eventually interact with real-time future multi-user features.
-   The underlying map representation should be designed so local edits
    can later be synchronized between Users.
-   Poor state management can make undo/redo and future multi-user features difficult
    to implement.

------------------------------------------------------------------------

## US-06 --- Node-Level Comment & Partial Regeneration

**Epic:** Epic 3 --- Interactive Canvas Editing

### Front of the Card

**User Story**

> As a User, I want to select one node and attach a comment or follow-up
> instruction to it, so that only that branch is refined without
> disturbing the rest of the map I have already arranged.

### Acceptance Criteria

#### Scenario 1 --- Scoped Regeneration

**Given** a selected node\
**When** I attach an instruction and confirm\
**Then** only that node's branch is regenerated\
**And** the rest of the map's structure and layout is preserved.

#### Scenario 2 --- Change Visibility

**Given** a partial regeneration is in progress\
**When** it completes\
**Then** the User can identify which nodes changed.

#### Scenario 3 --- Failed Partial Regeneration

**Given** partial regeneration fails\
**When** the request returns an error\
**Then** the existing branch remains unchanged\
**And** the User can retry.

### Back of the Card

**Technical Notes**

-   Implements FR-14, FR-15, and FR-36.
-   The LLM Service should receive only the relevant node/branch context
    rather than the entire map where possible.
-   The exact definition of a **branch** must be established:
    -   Selected node only
    -   Selected node plus descendants
    -   Selected node plus relevant ancestors
    -   Another explicitly defined subgraph
-   The regeneration operation should preserve node IDs and positions
    for unaffected content.
-   Changed nodes should have a clear version/change marker so the UI
    can communicate what was regenerated.

**Validation / Test Strategy**

-   Verify that unrelated branches remain unchanged.
-   Verify that unaffected node positions remain unchanged.
-   Verify that only the intended branch is included in the generation
    context.
-   Test branch-boundary edge cases.
-   Test failure and rollback behaviour.

**Dependencies / Risks**

-   Precisely defining the **branch boundary** requires UX/product
    confirmation; see DR-03.
-   Incorrect scoping could cause the LLM to change unrelated content.
-   Partial regeneration must remain scoped to the selected branch and should
    not alter unrelated map content.

------------------------------------------------------------------------

## US-06B --- Conversational Chat Assistant & Action Proposals

**Epic:** Epic 3 --- Mind Map Editing & Refinement  
*(Added afterwards in Sprint 2)*

### Front of the Card

**User Story**

> As a User, I want to chat interactively with an AI assistant about my
> mind map and receive actionable proposals (such as adding, editing,
> deleting, or elaborating nodes), so that I can iteratively refine the
> structure without manual node creation or whole-map regeneration.

### Acceptance Criteria

#### Scenario 1 --- Conversational Refinement & Proposal Generation

**Given** a mind map is open on the canvas\
**When** I ask the chat assistant to suggest additional subtopics or elaborate a node\
**Then** the assistant generates a structured action proposal (`add_node`, `edit_node`, `delete_node`, or `elaborate_node`)\
**And** the proposal appears in the chat message with clear preview buttons.

#### Scenario 2 --- User Confirmation of Action Proposal

**Given** a pending action proposal in the chat\
**When** I click **Apply / Confirm**\
**Then** the proposed modification is applied directly to the canvas graph\
**And** the proposal badge updates to reflect its confirmed state.

#### Scenario 3 --- User Dismissal of Action Proposal

**Given** a pending action proposal in the chat\
**When** I click **Dismiss**\
**Then** the proposal is marked as dismissed\
**And** the canvas graph remains completely unchanged.

### Back of the Card

**Technical Notes**

-   Added afterwards during Sprint 2 (S2-BE1, S2-BE2, S2-BE3, S2-FE1, S2-FE2, S2-FE3).
-   Persisted via `chat_threads` (one-to-one with `mind_maps`) and `chat_messages` tables.
-   Proposals contain structured payloads validating against `EditProposal` models.
-   The canvas is never mutated until the user explicitly confirms the proposal.

**Validation / Test Strategy**

-   Test creation of chat messages and action proposals.
-   Test proposal execution against graph endpoints.
-   Test dismissal state persistence.
-   Verify invalid proposals (e.g. deleting the root node) are rejected with HTTP 400.

------------------------------------------------------------------------

## US-06C --- Short-Term Conversational Memory & Context Window Management

**Epic:** Epic 3 --- Mind Map Editing & Refinement  
*(Added afterwards in Sprint 2)*

### Front of the Card

**User Story**

> As a User, I want the chat assistant to remember previous messages in the
> current map conversation while remaining fast and within token limits, so
> that I can have a natural, multi-turn refinement experience.

### Acceptance Criteria

#### Scenario 1 --- Multi-Turn Follow-Ups

**Given** an existing conversation thread on a mind map\
**When** I ask a follow-up question referencing an earlier answer\
**Then** the assistant uses the conversation history to understand the context.

#### Scenario 2 --- Context Window Protection

**Given** a long conversation or a large uploaded reference document\
**When** prompt assembly occurs\
**Then** the system extracts structured abstract excerpts (`format_seed_content`) and maintains a sliding history window\
**And** avoids exceeding model input token limits (preventing HTTP 413).

### Back of the Card

**Technical Notes**

-   Added afterwards in Sprint 2 (LLM-413, LLM-429).
-   Implements sliding conversation window and abstract-aware document excerpting.
-   Enforces provider output clamps (e.g. Qwen OTPM clamp).

**Validation / Test Strategy**

-   Test multi-turn context retention.
-   Unit-test `format_seed_content` heading and abstract extraction.
-   Verify total assembled prompt token size remains below model ceilings.

------------------------------------------------------------------------

# Epic 4 --- Export & Persistence

## US-07 --- Export a Mind Map

**Epic:** Epic 4 --- Export & Persistence

### Front of the Card

**User Story**

> As a User, I want to export my mind map as a PDF or PNG, so that I can
> use it outside the tool to share, print, or include it in other work.

### Acceptance Criteria

#### Scenario --- Export to File

**Given** a completed mind map\
**When** I choose **Export → PDF** or **Export → PNG**\
**Then** a correctly rendered file is produced through the export
service/API\
**And** the exported file reflects the current canvas state.

#### Scenario --- Large Map Export

**Given** a mind map is larger than the visible canvas\
**When** I export it\
**Then** the resulting file includes the complete map rather than only
the currently visible viewport.

#### Scenario --- Export Failure

**Given** the export service is unavailable\
**When** I attempt to export\
**Then** the system reports the failure clearly\
**And** my saved map remains available.

### Back of the Card

**Technical Notes**

-   Implements FR-19 and FR-38.
-   Export should use the persisted map structure and current layout.
-   PDF and PNG rendering should preserve node positions, edges, text,
    and visual hierarchy.
-   Consider configurable export options such as:
    -   Page size
    -   Orientation
    -   Background
    -   Scale/quality
    -   Entire-map vs. selected-area export
-   Generated files should not expose information that the User does not
    have permission to access.

**Validation / Test Strategy**

-   Compare exported node/edge layout against the on-screen canvas.
-   Test PDF and PNG output separately.
-   Test very large maps.
-   Test long node labels and overlapping/edge cases.
-   Verify that export respects the latest saved edits.
-   Test third-party API failure and timeout.

**Dependencies / Risks**

-   Third-party export API downtime or rate limits are external
    dependencies.
-   Large maps may require substantial rendering time or memory.
-   Export quality can differ between browser/canvas rendering and
    server-side rendering.

------------------------------------------------------------------------

## US-08 --- Save and Find Past Mind Maps

**Epic:** Epic 4 --- Export & Persistence

### Front of the Card

**User Story**

> As a User, I want to save my mind map and search for it later, so that
> I can return to and continue work I have already started.

### Acceptance Criteria

#### Scenario --- Save & Search

**Given** a saved map\
**When** I search by title or topic\
**Then** it appears in my results\
**And** it reopens in its last-edited state.

#### Scenario --- Reopen

**Given** a previously saved map\
**When** I open it\
**Then** its node content, relationships, and saved positions are
restored.

#### Scenario --- Save Failure

**Given** the system cannot save a map\
**When** I attempt to save\
**Then** the system indicates that the save failed\
**And** avoids falsely presenting the map as permanently saved.

### Back of the Card

**Technical Notes**

-   Implements FR-17 and FR-18.
-   Persist:
    -   Map metadata
    -   Node content
    -   Edges/relationships
    -   Node positions/layout
    -   Relevant generation/version information
    -   Ownership and permissions
-   Search should initially support title/topic matching and can later
    be extended to content search.
-   Autosave may be considered to reduce accidental data loss, provided
    the UX clearly communicates save state.
-   Saved maps must be isolated by User/permission boundaries.

**Validation / Test Strategy**

-   Perform round-trip save/reload tests.
-   Verify node positions and content are preserved.
-   Verify search by title and topic.
-   Test duplicate/similar titles.
-   Test save conflicts and interrupted saves.
-   Verify unauthorized Users cannot retrieve another User's map.

**Dependencies / Risks**

-   Database/storage availability.
-   Data consistency between canvas state and persisted state.
-   Search performance may become a concern as the number of saved maps
    grows.

------------------------------------------------------------------------

## US-08B --- Map Snapshots, Version History & Safe Restore

**Epic:** Epic 4 --- Export & Persistence  
*(Added afterwards in Sprint 2)*

### Front of the Card

**User Story**

> As a User, I want point-in-time snapshots of my mind map saved
> automatically on generation/edits or on demand, so that I can browse
> version history and safely restore a previous graph state if an edit or
> regeneration is unsatisfactory.

### Acceptance Criteria

#### Scenario 1 --- Browsing Version History

**Given** a mind map that has undergone edits or generations\
**When** I open the Version History panel\
**Then** I see an ordered list of previous snapshots with timestamps, node counts, and edge counts.

#### Scenario 2 --- Safe Restore with Collision Protection

**Given** a snapshot in the history list\
**When** I choose to restore it\
**Then** the current canvas graph is replaced with the snapshot data\
**And** the version token (`If-Match`) is updated to prevent concurrent edit collisions.

### Back of the Card

**Technical Notes**

-   Added afterwards in Sprint 2 (S2-DB3, S2-BE5, S2-FE6).
-   Persisted in `map_snapshots` table with `snapshot_data jsonb`.
-   Double-click restore guards and optimistic UI updates.

**Validation / Test Strategy**

-   Test snapshot creation on generation and canvas edits.
-   Test restoring snapshot and verifying node/edge counts match.
-   Verify restore updates the map's updated_at and version counter.

------------------------------------------------------------------------

# Epic 5 --- Collaboration & Shared Workspaces

## US-09 --- Shared Mind Maps & Multi-User Collaboration

**Epic:** Epic 5 --- Collaboration & Shared Workspaces  
*(Decision: Not Implemented / Descope)*

> [!WARNING]
> **Implementation Status: Descope / Not Implemented**  
> **Decision & Rationale:** Due to high architectural complexity, real-time synchronization overhead, state reconciliation challenges, and strict sprint time constraints, it was decided **not to implement** multi-user collaboration in Sprint 3. The platform strictly maintains single-user per map ownership with database Row Level Security (RLS) isolation.

### Front of the Card

**User Story**

> As a User, I want to share my mind map with specific collaborators via
> email with view or edit permissions, so that we can work on shared ideas
> together.

### Back of the Card

**Technical Notes**

-   Originally proposed under Module F (FR-20, FR-23).
-   Deferred indefinitely to post-sprint backlog due to complexity and sprint constraints.

------------------------------------------------------------------------

## US-10 --- Real-Time Concurrent Collaboration & Conflict Synchronization

**Epic:** Epic 5 --- Collaboration & Shared Workspaces  
*(Decision: Not Implemented / Descope)*

> [!WARNING]
> **Implementation Status: Descope / Not Implemented**  
> **Decision & Rationale:** Because of the complex engineering requirements of Operational Transformation (OT) or Conflict-Free Replicated Data Types (CRDTs), concurrent edit locking, and presence tracking, it was decided **not to implement** real-time multi-user concurrent editing in Sprint 3.

### Front of the Card

**User Story**

> As a User, I want to see collaborators' edits and presence on the canvas
> in real time without conflicting overwrites, so that we can brainstorm
> synchronously.

### Back of the Card

**Technical Notes**

-   Originally proposed under Module F (FR-21, FR-22, FR-24, FR-25).
-   Deferred to backlog. The application uses single-owner pessimistic locking/version tokens (`If-Match`) instead.

------------------------------------------------------------------------

# Epic 6 --- Admin & Platform Operations

## US-11 --- Admin Oversight

**Epic:** Epic 6 --- Admin & Platform Operations  
*(Implemented in Sprint 3: S3-DB1, S3-BE1, S3-BE2, S3-BE3, S3-FE1)*

### Front of the Card

**User Story**

> As an Admin, I want to manage user accounts and view aggregate
> platform usage, so that I can moderate the platform and understand how
> it is being used without accessing Users' private map content.

### Acceptance Criteria

#### Scenario --- Aggregate-Only Visibility

**Given** the admin panel\
**When** I view usage logs and analytics\
**Then** I see aggregate activity, such as generation counts, active users, and failure rates\
**And** I cannot view or select the raw content of Users' maps, chat messages, or uploads.

#### Scenario --- User Account Management

**Given** an Admin has the required `admin` role in `profiles.role`\
**When** they manage a User account (suspend, unsuspend, delete)\
**Then** the action succeeds and is recorded in `admin_audit_log` with actor, action, and target ID.

#### Scenario --- Admin Authorization

**Given** a normal User attempts to access the admin panel or admin endpoints\
**When** they make the request\
**Then** access is denied with HTTP 403 Forbidden.

### Back of the Card

**Technical Notes**

-   Implements FR-26 and FR-27; delivered via Sprint 3 issues S3-DB1, S3-BE1, S3-BE2, S3-BE3, S3-FE1.
-   Role defined in `profiles.role` (`user` vs `admin`, default `user`).
-   Admin audit trail stored in `admin_audit_log`.
-   **Zero SELECT privacy boundary:** PostgreSQL RLS and aggregate views enforce that administrators have zero SELECT capability on private user content (`mind_maps`, `nodes`, `edges`, `chat_messages`, `agent_memories`).

**Validation / Test Strategy**

-   Automated pgTAP tests prove the admin SELECT restriction holds on all content tables.
-   FastAPI dependency `require_admin_role()` unit tests.
-   Admin user management and analytics endpoint tests.
-   Audit log insertion tests for every administrative write.

**Dependencies / Risks**

-   Incorrect role configuration can expose administrative
    functionality.
-   Aggregate analytics should avoid unintentionally revealing private
    information.
-   See Conflict #5 regarding administrative visibility and privacy.

------------------------------------------------------------------------

# Epic 7 --- Developer & QA Enablement

## US-12 --- Configure the Generation Pipeline

**Epic:** Epic 7 --- Developer & QA Enablement

### Front of the Card

**User Story**

> As a Developer, I want to configure the LLM integration and add or
> update tools available to the generation engine, so that I can tune
> model behaviour and extend the system with new research tools over
> time.

### Acceptance Criteria

#### Scenario --- Extend the Toolset

**Given** the developer configuration\
**When** I add a new tool, such as a future data source\
**Then** the generation engine can invoke it without requiring changes
to unrelated core orchestration code.

#### Scenario --- Configure LLM Service

**Given** a supported LLM configuration\
**When** the Developer updates the configured model/service\
**Then** the generation pipeline uses the configured integration without
changing unrelated application logic.

#### Scenario --- Tool Failure

**Given** an optional research tool is unavailable\
**When** the generation engine attempts to use it\
**Then** the system handles the failure according to the tool's defined
fallback behaviour.

### Back of the Card

**Technical Notes**

-   Implements FR-28, FR-29, FR-30, and FR-31.
-   Use an abstraction/interface around LLM providers and external
    tools.
-   Tool configuration should be separate from core generation
    orchestration.
-   Secrets/API keys must be stored outside source code.
-   Tool definitions should specify inputs, outputs, authentication
    requirements, timeout behaviour, and failure handling.
-   New tools should be independently testable.

**Validation / Test Strategy**

-   Add a mock tool and verify it can be called without modifying core
    orchestration logic.
-   Test tool registration and configuration.
-   Test invalid configuration.
-   Test unavailable tools and timeout behaviour.
-   Test switching between supported LLM configurations.
-   Verify secrets are not exposed in logs or API responses.

**Dependencies / Risks**

-   Provider-specific APIs can introduce coupling if abstraction
    boundaries are weak.
-   Tool proliferation can increase latency and cost.
-   Poorly isolated integrations can make future provider changes
    expensive.

------------------------------------------------------------------------

## US-13 --- Test in Isolation

**Epic:** Epic 7 --- Developer & QA Enablement

### Front of the Card

**User Story**

> As a QA/Tester, I want to run generation and editing test cases against a
> non-production environment with mockable LLM and third-party responses, so
> that I can verify system behaviour without
> depending on live external services or affecting real User data.

### Acceptance Criteria

#### Scenario --- Isolated Test Run

**Given** the test environment\
**When** I run the predefined test suite with mocked LLM/tool responses\
**Then** pass/fail results are recorded\
**And** relevant error logs are accessible.

#### Scenario --- Repeatable Tests

**Given** the same test inputs and mocked dependencies\
**When** I run the test suite multiple times\
**Then** the tests produce consistent results unless nondeterminism is
explicitly being tested.

#### Scenario --- External Service Independence

**Given** an external LLM or third-party API is unavailable\
**When** the test suite runs\
**Then** tests that use mocks can still execute without depending on the
live service.

### Back of the Card

**Technical Notes**

-   Implements FR-32, FR-33, FR-34, and NFR-08.
-   Provide a dedicated non-production/test environment.
-   External services should be mockable at clear integration
    boundaries.
-   Test data should be isolated from production data.
-   Automated tests should cover unit, integration, API, UI, and
    end-to-end scenarios as appropriate.
-   Test runs should generate actionable logs and failure information.
-   Where LLM behaviour itself is being tested, use fixed fixtures or
    controlled test cases where possible.

**Validation / Test Strategy**

-   This story provides validation infrastructure for the rest of the
    backlog.
-   Verify that tests can run without live external APIs.
-   Verify mocked LLM/tool responses.
-   Test error logging and test-result reporting.
-   Include regression tests for previously fixed defects.
-   Run authorization and privacy tests as part of the automated suite.
-   Include regression tests for all implemented generation and editing workflows.

**Dependencies / Risks**

-   Poorly isolated tests can produce flaky results.
-   Mock behaviour can diverge from real provider behaviour, so selected
    integration tests against controlled real services may still be
    necessary.
-   Test environments must never accidentally operate on production User
    data.

------------------------------------------------------------------------

# Epic 8 --- Conversational Agent & Memory

## US-14 --- Long-Term Agent Memory & Personalization

**Epic:** Epic 8 --- Conversational Agent & Memory  
*(Added afterwards in Sprint 3: S3-DB2, S3-BE4, S3-L3, S3-FE2)*

### Front of the Card

**User Story**

> As a User, I want the AI assistant to remember my design preferences, past
> map decisions, and project facts across sessions and across different maps,
> so that generated mind maps and chat recommendations automatically tailor to
> my personal workflow.

### Acceptance Criteria

#### Scenario 1 --- Asynchronous Memory Extraction

**Given** a mind map generation or chat turn finishes\
**When** the worker processes the turn\
**Then** an extraction node identifies durable preferences, decisions, or facts\
**And** asynchronously proposes them to `agent_memories` via the backend without adding user-facing latency.

#### Scenario 2 --- Token-Budgeted Memory Retrieval

**Given** a user with stored memories generates a map or asks a chat question\
**When** prompt assembly occurs\
**Then** the top-k most relevant memories are retrieved and injected\
**And** memory tokens are strictly capped at $\le 10\%$ of the model context window.

#### Scenario 3 --- User Memory Management & Privacy

**Given** the user navigates to `/memory`\
**When** they view their memories\
**Then** they can view, disable, enable, or delete individual memories\
**And** export their memories as a GDPR JSON bundle\
**And** no other user or administrator can read their memory records.

### Back of the Card

**Technical Notes**

-   Added afterwards in Sprint 3 (S3-DB2, S3-BE4, S3-L3, S3-FE2).
-   Persisted in PostgreSQL table `agent_memories` with `user_id`, `map_id` (nullable), `kind`, `importance`, and `last_accessed_at`.
-   Worker remains database-free; requests memories through backend internal endpoints.
-   Protected by PostgreSQL Row Level Security (RLS); administrators have zero SELECT access.

**Validation / Test Strategy**

-   pgTAP tests proving RLS isolation: user sees only own memories, admin sees none.
-   Backend memory CRUD and GDPR export endpoint tests.
-   Worker memory proposal deduplication and importance decay unit tests.

------------------------------------------------------------------------

# Epic 9 --- Observability & Operational Quality

## US-15 --- LangSmith Observability & Pipeline Tracing

**Epic:** Epic 9 --- Observability & Operational Quality  
*(Added afterwards in Sprint 3: S3-OBS)*

### Front of the Card

**User Story**

> As a Developer and Platform Engineer, I want all LLM calls, tool executions,
> and LangGraph state transitions captured as named spans in LangSmith, so that
> I can debug latency bottlenecks, trace token consumption, and evaluate map
> quality against benchmark datasets.

### Acceptance Criteria

#### Scenario 1 --- Named Span Tracing

**Given** `LANGCHAIN_TRACING_V2=true` is set in the environment\
**When** a generation, regeneration, chat turn, or research loop runs\
**Then** every LangGraph node transition and tool invocation is recorded as a named span in LangSmith\
**And** traces are routed to `mindweave-dev` or `mindweave-prod` based on environment.

#### Scenario 2 --- Benchmark Dataset Evaluation

**Given** the evaluation seed dataset\
**When** quality-gate runs are triggered\
**Then** node count, graph depth, schema validity, and latency metrics are automatically logged to LangSmith datasets.

#### Scenario 3 --- Graceful Degradation in Clean CI

**Given** `LANGCHAIN_TRACING_V2` is absent or set to false\
**When** automated CI tests run without API keys\
**Then** tracing decorators act as a zero-overhead no-op\
**And** tests pass cleanly without network calls.

### Back of the Card

**Technical Notes**

-   Added afterwards in Sprint 3 (S3-OBS).
-   Instrumented via `@traceable` decorators on `generate_node`, `chat_turn_task`, `regenerate_node_task`, and research planner nodes.
-   API keys strictly managed via environment variables and CI secrets.

**Validation / Test Strategy**

-   Verify trace metadata emission when tracing is enabled.
-   Verify zero exception / no-op behavior when environment variable is omitted.
-   Confirm no API keys or secrets are logged in traces.

------------------------------------------------------------------------

## US-16 --- Dynamic Context Management & Token Budgeting

**Epic:** Epic 7 --- Developer & QA Enablement  
*(Added afterwards in Sprint 3: S3-L7)*

### Front of the Card

**User Story**

> As a Developer and User, I want the generation engine to automatically manage
> and partition context window slots across prompts, memories, RAG chunks, and
> chat history according to provider limits, so that generations never fail due
> to HTTP 413 (Payload Too Large) or HTTP 429 (Rate/Token quota exceeded).

### Acceptance Criteria

#### Scenario 1 --- Dynamic Context Partitioning

**Given** a generation or chat request with multiple context inputs (seed, memory, RAG, history)\
**When** `ContextBudgetManager` prepares the prompt payload\
**Then** it dynamically partitions available tokens across components according to active provider constraints.

#### Scenario 2 --- Hierarchical Pruning on Ceiling Overflow

**Given** assembled context exceeds the active provider input ceiling\
**When** pruning is applied\
**Then** items are pruned in strict priority order: (1) lowest-scoring RAG chunks, (2) oldest chat messages beyond sliding window, (3) dense external tool payloads\
**And** essential system instructions and core topic seeds are strictly preserved.

### Back of the Card

**Technical Notes**

-   Added afterwards in Sprint 3 (S3-L7).
-   Scales limits dynamically for Groq (8k/6k input ceiling), Qwen OTPM clamp, and Gemini Flash (32k/128k).
-   Emits token slot breakdowns to `state["prompt_items_log"]` and span attributes in LangSmith.

**Validation / Test Strategy**

-   Unit tests in `llm/tests/test_token_budgeting.py` verifying slot bounds and degradation order.
-   Mock tests simulating near-limit payloads and ensuring no HTTP 413/429 errors.

------------------------------------------------------------------------

## US-17 --- Multimodal Seed Intake & Expanded Document Formats

**Epic:** Epic 1 --- Onboarding & Seed Capture  
*(Added afterwards in Sprint 3: S3-BE7, S3-FE4, S3-FE5, S3-DB3, S3-T4)*

### Front of the Card

**User Story**

> As a User, I want to submit public web URLs, DOCX files, and PPTX slide decks
> as generation seeds and select my map's purpose (General, Study, Research), so
> that the system can synthesize information from modern document and research
> sources.

### Acceptance Criteria

#### Scenario 1 --- Public URL Seed Intake

**Given** the create-map interface\
**When** I enter a valid public web URL in the seed URL field\
**Then** the backend validates the address against an SSRF blocklist (blocking loopback and private IPs)\
**And** fetches the page content for mind map generation.

#### Scenario 2 --- Office Document Ingestion

**Given** an uploaded DOCX or PPTX document\
**When** the file is submitted\
**Then** the system extracts structural headings and slide titles\
**And** generates structured concepts representing the presentation or report.

#### Scenario 3 --- Map Generation Purpose Selection

**Given** the create-map screen\
**When** I choose **Research** as the map purpose\
**Then** the backend forwards `purpose="research"` to the generation pipeline\
**And** activates academic literature search tools (arXiv and Scholar).

### Back of the Card

**Technical Notes**

-   Added afterwards in Sprint 3 (S3-BE7, S3-FE4, S3-FE5, S3-DB3, S3-T4).
-   SSRF protection blocks RFC-1918, loopback, link-local, and zero hosts with max 3 redirects and 1MB size limit.
-   Document extractors enforce size limits (20MB) and zip-bomb safety.

**Validation / Test Strategy**

-   SSRF unit tests in `test_s3_be7_intake.py` verifying private IP rejection.
-   MIME sniffing tests for DOCX/PPTX.
-   Purpose selector UI unit tests and backend routing tests.

------------------------------------------------------------------------

## US-18 --- Advanced Research Tools & Subgraph Execution

**Epic:** Epic 2 --- AI-Driven Mind Map Generation  
*(Added afterwards in Sprint 3: S3-A2, S3-A3, S3-A4, S3-T1, S3-T2, S3-T3)*

### Front of the Card

**User Story**

> As a User, I want the AI generator to search Wikipedia, clean full-text web
> pages, and academic literature repositories when exploring a topic, so that the
> generated mind map is authoritative, grounded, and attributed with verifiable
> citations.

### Acceptance Criteria

#### Scenario 1 --- Bounded Research Agent Subgraph

**Given** a topic-mode seed\
**When** research is triggered\
**Then** a dedicated research subgraph plans queries, executes parallel tool calls (Tavily, Wikipedia, Scholar), deduplicates sources by canonical URL/DOI, caches results in Redis, and checks sufficiency in $\le 2$ rounds.

#### Scenario 2 --- Wikipedia Attribution

**Given** Wikipedia is used during generation\
**When** nodes are created from Wikipedia content\
**Then** CC BY-SA attribution links are stored in chunk metadata and displayed in the frontend `NodeInspector` panel.

#### Scenario 3 --- Shared Branch Expansion

**Given** full map generation, node regeneration, or chat elaboration\
**When** subtrees are expanded\
**Then** all three operations use the unified `expand_branch` function, ensuring consistent quality gates and source tagging.

### Back of the Card

**Technical Notes**

-   Added afterwards in Sprint 3 (S3-A2, S3-A3, S3-A4, S3-T1, S3-T2, S3-T3).
-   Redis result cache with 1h TTL; bounded research time budget (10s).
-   Graceful fallback: returns degraded-run warning if any research tool fails without breaking map generation.

**Validation / Test Strategy**

-   Unit tests for Wikipedia REST API integration and CC BY-SA links.
-   Page reader HTML sanitization tests.
-   Scholar tool contract and policy tests.

------------------------------------------------------------------------

## US-19 --- Vision Model for Image Ingestion (Gemini Flash)

**Epic:** Epic 1 --- Onboarding & Seed Capture  
*(Added afterwards in Sprint 3: S3-L6, Deferred from Sprint 2 L-1)*

### Front of the Card

**User Story**

> As a User, I want uploaded images and diagram seeds processed by a modern
> multimodal vision model (Google Gemini Flash), so that diagrams, textbook
> figures, and handwritten notes in images are accurately recognized into
> structured mind map concepts.

### Acceptance Criteria

#### Scenario 1 --- Vision API Extraction

**Given** an uploaded diagram or handwritten note image\
**When** image ingestion runs\
**Then** Google Gemini Flash processes the image and extracts rich structured text concepts.

#### Scenario 2 --- Automatic Fallback to Tesseract OCR

**Given** the vision API is temporarily unavailable or reaches quota limits\
**When** image extraction is attempted\
**Then** the system automatically falls back to local Tesseract OCR\
**And** generates a seed from extracted text without failing the user's request.

### Back of the Card

**Technical Notes**

-   Added afterwards in Sprint 3 (S3-L6; deferred from Sprint 2 L-1).
-   Pluggable extractor registry (`S3-R1`) configured via `IMAGE_EXTRACTOR=gemini|openrouter|tesseract`.
-   Eliminates legacy forced-501 behavior for image uploads.

**Validation / Test Strategy**

-   Mocked API failure test verifying transparent Tesseract OCR fallback.
-   Extractor registry unit tests verifying MIME mapping and heading emission.

------------------------------------------------------------------------

# 2. Cross-Cutting Definition of Done

The following checklist can be applied to each story before it is
considered complete:

-   [ ] Acceptance criteria are implemented.
-   [ ] Automated tests cover the main success path.
-   [ ] Important failure/error paths are tested.
-   [ ] Authorization and privacy requirements are verified where
    applicable.
-   [ ] Data persistence/reload behaviour is tested where applicable.
-   [ ] External services are mocked or stubbed in isolated tests.
-   [ ] User-facing errors are understandable and do not expose
    sensitive technical information.
-   [ ] Relevant logging/monitoring is implemented without logging
    private User content unnecessarily.
-   [ ] Documentation/configuration is updated where the story
    introduces a new integration or behaviour.
-   [ ] No known regression is introduced into existing functionality.

------------------------------------------------------------------------

# 3. Important Open Design Questions

Several areas in the backlog depend on decisions that should be
finalized before implementation reaches those areas.

## 3.1 Partial Regeneration Boundary

For US-06, define exactly what constitutes a **branch**.

Possible interpretations include:

-   The selected node only.
-   The selected node and all descendants.
-   The selected node, descendants, and selected contextual ancestors.
-   A manually selected subgraph.

The chosen definition should be reflected consistently in the API and
UI.


## 3.2 Multi-User Collaboration & Synchronization Scope Decision

For US-09 and US-10, the project evaluated multi-user real-time collaboration.

**Final Decision for Sprint 3:**
Due to high architectural complexity, real-time operational transformation/CRDT synchronization overhead, concurrency state reconciliation, and strict sprint deadlines, multi-user concurrent editing and real-time conflict synchronization were officially **not implemented**. Mind maps strictly remain single-user per map ownership, enforced by PostgreSQL Row Level Security (RLS). Single-owner version conflicts during save and restore are handled deterministically via `If-Match` version checking.

## 3.3 Admin Privacy Boundary

For US-11, explicitly define:

-   Which aggregate metrics administrators can see.
-   Whether any exceptional support access to User content exists.
-   How such access is authorized.
-   What is recorded in the audit log.

**Final Implementation in Sprint 3:**
Administrators have access to aggregate metrics only (generation counts, active users, failure rates) via dedicated SQL views. Under no circumstances do administrators have SELECT permissions on raw user content tables (`mind_maps`, `nodes`, `edges`, `chat_messages`, `agent_memories`). Every administrative account action (suspend, unsuspend, delete) appends to `admin_audit_log`.

## 3.4 Generation Versioning

US-04 and US-06 would benefit from a clear version model.

At minimum, consider distinguishing:

-   Original seed
-   Generated map version
-   User-edited version
-   Regenerated version
-   Partially regenerated version

This can prevent accidental loss of useful work and simplify debugging. Implemented in Sprint 2 via `map_snapshots` and optimistic concurrency version checking.

## 3.5 External Service Failure Policy

The system depends on several external services, including
authentication, LLMs, research tools, and export services.

For each integration, define:

-   Timeout
-   Retry policy
-   Maximum retries
-   User-facing failure message
-   Whether a fallback exists
-   Whether the operation can be resumed
-   Whether the failure should be recorded for monitoring

------------------------------------------------------------------------

# 4. Backlog Dependency Overview

| Story | Feature / Scope | Main Dependency / Risk | Implementation Status |
|---|---|---|---|
| **US-01** | Sign Up / Sign In | Google OAuth and secure authentication | ✅ Implemented (Sprint 1) |
| **US-02** | Submit a Raw Seed | File parsing and input normalization | ✅ Implemented (Sprint 1) |
| **US-03** | Generate Mind Map from Seed | LLM service and research tools | ✅ Implemented (Sprint 1) |
| **US-04** | Whole Map Regeneration | LLM cost/rate limits and generation versioning | ✅ Implemented (Sprint 1) |
| **US-05** | Direct-Manipulation Canvas | Canvas state management and layout engine | ✅ Implemented (Sprint 2) |
| **US-06** | Node Comment & Partial Regen | Scoped branch context and boundary decisions | ✅ Implemented (Sprint 2) |
| **US-06B** | Conversational Chat Assistant | Action proposal execution & user confirmation | ✅ Implemented (Sprint 2 Mid-Sprint) |
| **US-06C** | Short-Term Conversational Memory | Chat history sliding window & seed excerpting | ✅ Implemented (Sprint 2 Mid-Sprint) |
| **US-07** | Export a Mind Map | Headless export microservice & client fallback | ✅ Implemented (Sprint 2) |
| **US-08** | Save & Find Past Mind Maps | Asyncpg parameterized search and RLS boundaries | ✅ Implemented (Sprint 2) |
| **US-08B** | Map Snapshots & Version History | `map_snapshots` table & version token restore | ✅ Implemented (Sprint 2 Mid-Sprint) |
| **US-09** | Shared Maps / Collaboration | Permission control & invitation workflow | ❌ Descope / Not Implemented (Complexity/Time) |
| **US-10** | Real-Time Sync & Presence | OT/CRDTs and concurrent conflict sync | ❌ Descope / Not Implemented (Complexity/Time) |
| **US-11** | Admin Oversight & RBAC | `profiles.role`, aggregate views & zero-SELECT RLS | ✅ Implemented (Sprint 3: S3-DB1/BE1-3/FE1) |
| **US-12** | Configure Generation Pipeline | Dynamic pipeline config API and admin UI | ✅ Implemented (Sprint 3: S3-BE8/FE7/L2) |
| **US-13** | Test in Isolation | Deterministic mocks, fixtures, and pgTAP tests | ✅ Implemented (Sprint 3: S3-L5) |
| **US-14** | Long-Term Agent Memory | `agent_memories` table, async extraction, GDPR | ✅ Implemented (Sprint 3: S3-DB2/BE4/L3/FE2) |
| **US-15** | LangSmith Observability | `@traceable` spans & automated eval datasets | ✅ Implemented (Sprint 3: S3-OBS) |
| **US-16** | Dynamic Context Budgeting | Provider-aware token clamps & hierarchical pruning | ✅ Implemented (Sprint 3: S3-L7) |
| **US-17** | Multimodal Document Intake | URL SSRF protection, DOCX/PPTX, Purpose Intake | ✅ Implemented (Sprint 3: S3-BE7/FE4-5/DB3/T4) |
| **US-18** | Advanced Research Subgraph | Wikipedia, Page Reader, Scholar, Branch Expand | ✅ Implemented (Sprint 3: S3-A2-4/T1-3) |
| **US-19** | Gemini Flash Vision Model | Multimodal vision extraction with Tesseract fallback | ✅ Implemented (Sprint 3: S3-L6/R1) |

------------------------------------------------------------------------

# 5. Summary

The backlog progresses from the basic User journey toward increasingly
sophisticated and resilient platform capabilities:

1.  **Onboarding and seed capture** establish the User and collect raw material (text, PDFs, images, URLs, DOCX, and PPTX).
2.  **AI generation and research** transform raw material into structured mind maps supported by Wikipedia, Web Page, and Scholar research tools.
3.  **Canvas editing and conversational refinement** give the User direct interactive control over the graph through direct canvas manipulation, snapshot version history, and an AI chat assistant with action proposals.
4.  **Export and persistence** allow the User to save, search, restore snapshots, and export their work to PDF/PNG via a dedicated headless service or client-side canvas fallback.
5.  **Admin operations** provide platform oversight and user account moderation while strictly guaranteeing User content privacy via database-enforced zero-SELECT rules.
6.  **Agent memory and personalization** enable the assistant to retain durable user preferences, past decisions, and project facts across sessions and maps with full GDPR user controls.
7.  **Developer, QA, and observability enablement** instrument the entire pipeline with LangSmith tracing, dynamic context token budgeting preventing HTTP 413/429 errors, and deterministic test fixtures for reliable CI.
