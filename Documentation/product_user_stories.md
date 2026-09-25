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

# Epic 6 --- Admin & Platform Operations

## US-11 --- Admin Oversight

**Epic:** Epic 6 --- Admin & Platform Operations

### Front of the Card

**User Story**

> As an Admin, I want to manage user accounts and view aggregate
> platform usage, so that I can moderate the platform and understand how
> it is being used without accessing Users' private map content.

### Acceptance Criteria

#### Scenario --- Aggregate-Only Visibility

**Given** the admin panel\
**When** I view usage logs\
**Then** I see aggregate activity, such as generation counts\
**And** I cannot view the raw content of Users' maps or uploads.

#### Scenario --- User Account Management

**Given** an Admin has the required permissions\
**When** they manage a User account\
**Then** only explicitly supported account-management actions are
available.

#### Scenario --- Admin Authorization

**Given** a normal User attempts to access the admin panel\
**When** they make the request\
**Then** access is denied.

### Back of the Card

**Technical Notes**

-   Implements FR-26 and FR-27.
-   Admin privileges must be separate from normal User privileges.
-   Aggregate metrics should be designed so that administrators do not
    need raw map content to understand platform usage.
-   Administrative actions should be logged for auditability.
-   Sensitive User content should not be included in normal operational
    dashboards.
-   If support workflows require exceptional access to User content,
    that process should be explicitly defined and audited rather than
    silently exposing it.

**Validation / Test Strategy**

-   Confirm admin log views exclude raw User content.
-   Test role-based access control.
-   Verify normal Users cannot access administrative endpoints.
-   Test administrative action logging.
-   Review API responses to ensure private map data is not accidentally
    included.

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


## 3.3 Admin Privacy Boundary

For US-11, explicitly define:

-   Which aggregate metrics administrators can see.
-   Whether any exceptional support access to User content exists.
-   How such access is authorized.
-   What is recorded in the audit log.

## 3.4 Generation Versioning

US-04 and US-06 would benefit from a clear version model.

At minimum, consider distinguishing:

-   Original seed
-   Generated map version
-   User-edited version
-   Regenerated version
-   Partially regenerated version

This can prevent accidental loss of useful work and simplify debugging.

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

  Story   Main Dependency / Risk
  ------- ----------------------------------------------------
  US-01   Google OAuth and secure authentication
  US-02   File parsing and input normalization
  US-03   LLM service and research tools
  US-04   LLM cost/rate limits and generation versioning
  US-05   Canvas state management and future multi-user features
  US-06   Branch-definition decision and scoped LLM context
  US-07   Third-party export service and large-map rendering
  US-08   Persistent storage and search
  US-09   Authorization and permission model
  US-10   Real-time synchronization and conflict resolution
  US-11   Role-based access control and privacy boundaries
  US-12   LLM/tool abstraction and configuration
  US-13   Test environment and mockable integrations

------------------------------------------------------------------------

# 5. Summary

The backlog progresses from the basic User journey toward increasingly
complex platform capabilities:

1.  **Onboarding and seed capture** establish the User and collect raw
    material.
2.  **AI generation** turns that material into a structured mind map.
3.  **Canvas editing** gives the User direct control over the generated
    structure.
4.  **Export and persistence** allow the User to save, search, and reuse
    their work.
5.  **Admin operations** provide platform oversight while maintaining
    User privacy.
6.  **Developer and QA enablement** keeps the generation pipeline
    extensible and testable.

The most important architectural considerations that cut across multiple
stories are **secure authorization, structured map data, generation versioning,
external-service failure handling, testability, and extensibility**.
