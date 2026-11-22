# Functional Requirements

## 1. Overview

The **Mind Map Generator from Raw Ideas** provides functionality for users to submit raw ideas or reference material, generate AI-powered mind maps, edit and refine them, save and export them, and interact with a conversational assistant.

The system also provides functionality for **Administrators, Developers, and QA/Testers**, along with integrations for the **LLM Service**, observability platforms, and external third-party APIs.

> [!NOTE]
> **Evolution of Scope across Sprints:**
> - **Added in Sprint 2:** Interactive Chat Assistant with Action Proposals (Module L) and Short-Term Conversational Memory.
> - **Added in Sprint 3:** Long-Term Agent Personalization (Module L), LangSmith Observability & Tracing (Module M), Dynamic Token Budgeting, and Multimodal Document & URL Seed Intake (Module N).
> - **Descoped in Sprint 3:** Multi-user collaboration and real-time concurrent synchronization (Module F) were excluded from implementation due to architectural complexity, synchronization overhead, and strict sprint deadlines. The platform maintains strict single-user per map ownership with database Row Level Security (RLS).

The functional requirements are organized into the following modules:

* **Module A: Authentication & Onboarding**
* **Module B: Seed Capture**
* **Module C: AI-Driven Mind Map Generation**
* **Module D: Canvas & Direct-Manipulation Editing**
* **Module E: Persistence, Search & Export**
* **Module F: Collaboration (Descoped / Not Implemented)**
* **Module G: Admin**
* **Module H: Developer-Facing**
* **Module I: QA/Tester-Facing**
* **Module J: LLM Service Integration**
* **Module K: Third-Party API Integration**
* **Module L: Conversational Assistant & Memory (Added Afterwards)**
* **Module M: Observability & Monitoring (Added Afterwards)**
* **Module N: Multimodal & Document Intake (Added Afterwards)**

---

## 2. Functional Requirements Summary

| Module | Area | Requirements | Implementation Status |
| --- | --- | --- | --- |
| **Module A** | Authentication & Onboarding | FR-01 - FR-03 | ✅ Implemented (Sprint 1) |
| **Module B** | Seed Capture | FR-04 - FR-06 | ✅ Implemented (Sprint 1 & Sprint 3) |
| **Module C** | AI-Driven Mind Map Generation | FR-07 - FR-10 | ✅ Implemented (Sprint 1 & Sprint 3) |
| **Module D** | Canvas & Direct-Manipulation Editing | FR-11 - FR-16 | ✅ Implemented (Sprint 2) |
| **Module E** | Persistence, Search & Export | FR-17 - FR-19 | ✅ Implemented (Sprint 2) |
| **Module F** | Collaboration | FR-20 - FR-25 | ❌ Descope / Not Implemented (Complexity/Time) |
| **Module G** | Admin | FR-26 - FR-27 | ✅ Implemented (Sprint 3: S3-DB1, BE1-3, FE1) |
| **Module H** | Developer-Facing | FR-28 - FR-31 | ✅ Implemented (Sprint 3: S3-BE8, FE7, L2) |
| **Module I** | QA/Tester-Facing | FR-32 - FR-34 | ✅ Implemented (Sprint 3: S3-L5) |
| **Module J** | LLM Service Integration | FR-35 - FR-37 | ✅ Implemented (Sprint 1 & Sprint 3) |
| **Module K** | Third-Party API Integration | FR-38 - FR-41 | ✅ Implemented (Sprint 1, 2 & 3) |
| **Module L** | Conversational Assistant & Memory | FR-42 - FR-44 | ✅ Implemented (Sprint 2 Mid-Sprint & Sprint 3) |
| **Module M** | Observability & Tracing | FR-45 | ✅ Implemented (Sprint 3: S3-OBS) |
| **Module N** | Multimodal & Document Intake | FR-46 - FR-47 | ✅ Implemented (Sprint 3: S3-BE7, FE4-5, T4) |

---

## 3. Authentication & Onboarding

The system must provide secure authentication mechanisms so that users can create accounts and access their mind maps.

### FR-01 - User Sign Up

**Requirement:**
The system shall allow a User to sign up with an email and password.

**Stakeholder:** User

### FR-02 - User Login

**Requirement:**
The system shall allow a User to log in.

**Stakeholder:** User

### FR-03 - Google Sign In

**Requirement:**
The system shall allow a User to sign in directly with Google.

**Stakeholders:** User, Third Party API

---

## 4. Seed Capture - Raw Ideas Input

The system shall allow users to provide raw information in different forms. Uploaded reference material and free-text ideas can be combined into a single seed that is used for mind map generation.

### FR-04 - Upload Reference Material

**Requirement:**
The system shall allow a User to upload reference material in the form of images or PDFs.

**Stakeholder:** User

### FR-05 - Enter Topic and Free-Text Ideas

**Requirement:**
The system shall allow a User to enter a topic and free-text ideas.

**Stakeholder:** User

### FR-06 - Combine Input into a Seed

**Requirement:**
The system shall combine uploaded material and free text into a single seed for generation.

**Stakeholder:** User

---

## 5. AI-Driven Mind Map Generation

The core functionality of the system is to generate structured mind maps from user-provided seeds using an LLM engine and external research tools.

### FR-07 - Generate Mind Map

**Requirement:**
The system shall generate a mind map from a submitted seed using the LLM engine.

**Stakeholders:** User, LLM Service

### FR-08 - Invoke External Tools

**Requirement:**
The LLM engine shall be able to invoke external tools such as web search, arXiv search, and an extensible tool set during generation.

**Stakeholders:** LLM Service, Third Party API

### FR-09 - Regenerate or Adjust Whole Map

**Requirement:**
The system shall allow a User to regenerate or adjust the whole map by re-prompting.

**Stakeholder:** User

### FR-10 - Represent Structured Mind Map

**Requirement:**
The system shall represent generated output as structured nodes representing concepts and edges representing relationships.

**Stakeholders:** User, LLM Service

---

## 6. Canvas & Direct-Manipulation Editing

The system shall provide an interactive canvas that allows users to directly manipulate and refine their generated mind maps.

### FR-11 - Interactive Canvas

**Requirement:**
The system shall present the mind map on an interactive, navigable canvas.

**Stakeholder:** User

### FR-12 - Reposition Nodes

**Requirement:**
The system shall allow a User to drag and reposition nodes on the canvas.

**Stakeholder:** User

### FR-13 - Modify Nodes and Edges

**Requirement:**
The system shall allow a User to manually add, remove, or reconnect nodes and edges.

**Stakeholder:** User

### FR-14 - Add Node-Level Comments

**Requirement:**
The system shall allow a User to select an individual node and attach a comment or follow-up instruction to it.

**Stakeholder:** User

### FR-15 - Partial Node-Level Regeneration

**Requirement:**
The system shall regenerate only the selected node's branch based on its attached comment, without disturbing the rest of the map.

**Stakeholders:** User, LLM Service

### FR-16 - Undo and Redo

**Requirement:**
The system shall allow a User to undo and redo edits made to a mind map.

**Stakeholder:** User

---

## 7. Persistence, Search & Export

The system shall allow users to save their work, retrieve previously created mind maps, and export maps into commonly used formats.

### FR-17 - Save Mind Map

**Requirement:**
The system shall allow a User to save a mind map and its generation/chat history.

**Stakeholder:** User

### FR-18 - Search Saved Mind Maps

**Requirement:**
The system shall allow a User to view and search their previously saved mind maps.

**Stakeholder:** User

### FR-19 - Export Mind Map

**Requirement:**
The system shall allow a User to export a mind map as PDF, PNG, or other common formats.

**Stakeholders:** User, Third Party API

---

## 8. Collaboration (Descoped / Not Implemented in Sprint 3)

The collaboration functionality was originally proposed to allow multiple users to share and work on the same mind map concurrently.

> [!WARNING]
> **Status: Descope / Not Implemented in Sprint 3**  
> **Decision & Rationale:** Due to high architectural complexity, real-time synchronization overhead (OT/CRDT algorithms), race-condition reconciliation, and strict sprint deadlines, it was decided **not to implement** multi-user collaboration (FR-20 through FR-25) in Sprint 3. The platform strictly enforces single-user ownership with PostgreSQL Row Level Security (RLS) isolation. Single-owner concurrent edit protection is implemented via optimistic version token matching (`If-Match`).

### FR-20 - Share Mind Map

**Requirement:**
The system shall allow a User to share a mind map with other Users.

**Stakeholder:** User

### FR-21 - Concurrent Viewing

**Requirement:**
The system shall allow multiple Users to view the same mind map concurrently.

**Stakeholder:** User

### FR-22 - Concurrent Editing

**Requirement:**
The system shall allow multiple Users to edit the same mind map concurrently.

**Stakeholder:** User

### FR-23 - Manage Sharing Permissions

**Requirement:**
The system shall allow the owner of a mind map to control whether other Users it is shared with can view or edit it.

**Stakeholder:** User

### FR-24 - Real-Time Collaboration Notifications

**Requirement:**
The system shall notify collaborators in real time when a shared map is updated.

**Stakeholder:** User

### FR-25 - Detect Conflicting Edits

**Requirement:**
The system shall detect and surface conflicting concurrent edits to collaborators.


**Stakeholder:** User

---

## 9. Admin Functions

The system shall provide administrators with functionality for managing user accounts and monitoring aggregate platform activity while protecting user content.

### FR-26 - Manage User Accounts

**Requirement:**
The system shall allow an Admin to manage user accounts, including viewing, suspending, or removing accounts.

**Stakeholder:** Admin

### FR-27 - View Platform Usage

**Requirement:**
The system shall allow an Admin to view aggregate platform usage and activity logs without exposing the raw content of Users' maps or uploads.

**Stakeholder:** Admin

---

## 10. Developer-Facing Functions

The system shall provide Developers with the functionality required to configure, maintain, extend, and debug the generation pipeline.

### FR-28 - Configure LLM Integration

**Requirement:**
The system shall allow a Developer to configure and update the LLM integration, including API keys, model versions, and prompt templates.

**Stakeholder:** Developer

### FR-29 - Manage Generation Tools

**Requirement:**
The system shall allow a Developer to add or update tools available to the generation engine, including web search, arXiv, and future tools.

**Stakeholder:** Developer

### FR-30 - Access System Logs

**Requirement:**
The system shall provide a Developer with access to system logs and error reports for debugging.

**Stakeholder:** Developer

### FR-31 - Deploy Updates

**Requirement:**
The system shall allow a Developer to deploy updates without downtime that affects Users.

**Stakeholder:** Developer

---

## 11. QA / Tester Functions

The system shall provide QA/Testers with a separate environment and testing functionality to verify system behaviour without affecting production data.

### FR-32 — Separate Test Environment

**Requirement:**
The system shall provide a QA/Tester with access to a test environment separate from production data.

**Stakeholder:** QA / Tester

### FR-33 - Execute Test Cases

**Requirement:**
The system shall allow a QA/Tester to execute predefined test cases against generation, editing, and collaboration features and record pass/fail results.

**Stakeholder:** QA / Tester

### FR-34 - Access Error Logs

**Requirement:**
The system shall log errors and exceptions in a form a QA/Tester can access to file defect reports.

**Stakeholder:** QA / Tester

---

## 12. LLM Service Integration

The system shall provide an integration layer between the application and the LLM Service responsible for generating and refining mind maps.

### FR-35 - Send Seed to LLM

**Requirement:**
The system shall send the seed to the LLM Service and receive a structured mind map in return.

**Stakeholders:** User, LLM Service

### FR-36 - Support Scoped Regeneration

**Requirement:**
The system shall pass a node's attached comment and its branch context to the LLM Service to support scoped, partial regeneration.

**Stakeholders:** User, LLM Service

### FR-37 - Handle LLM Service Failures

**Requirement:**
The system shall detect a failed, delayed, or unavailable LLM Service response and handle it gracefully through mechanisms such as retry or a fallback message.

**Stakeholder:** LLM Service

---

## 13. Third-Party API Integration

The system shall integrate with external services required for authentication, research, generation, and export functionality.

### FR-38 - External Format Conversion

**Requirement:**
The system shall use a third-party API to convert and export a mind map into external formats.

**Stakeholder:** Third Party API

### FR-39 - Google OAuth Authentication

**Requirement:**
The system shall authenticate Users via Google OAuth where configured.

**Stakeholders:** User, Third Party API

### FR-40 - Research Tool Integration

**Requirement:**
The system shall use third-party research tools such as web search API and arXiv API as part of the generation pipeline.

**Stakeholders:** LLM Service, Third Party API

### FR-41 - Observe API Rate Limits

**Requirement:**
The system shall observe the rate limits and usage quotas of every integrated third-party API.

**Stakeholder:** Third Party API

---

## 14. Conversational Assistant & Memory (Module L — Added Afterwards)

The system shall provide conversational refinement and persistent memory so the assistant can iteratively update maps and personalize to user preferences.

### FR-42 - Interactive Chat Assistant & Action Proposals

**Requirement:**
The system shall provide an interactive conversational chat panel for each mind map, allowing Users to request structural changes. The assistant shall return structured action proposals (`add_node`, `edit_node`, `delete_node`, `elaborate_node`) that require explicit User confirmation or dismissal before modifying the live canvas graph.

**Stakeholders:** User, LLM Service

### FR-43 - Short-Term Conversational Context Window

**Requirement:**
The system shall persist conversation turns per mind map and maintain a dynamic sliding context window with abstract-aware seed excerpting to ensure multi-turn continuity without exceeding model input token ceilings.

**Stakeholders:** User, LLM Service

### FR-44 - Long-Term Agent Personalization & Memory

**Requirement:**
The system shall asynchronously extract durable user preferences, past decisions, and project facts from chat and generation turns, store them in a persistent `agent_memories` table, inject relevant memories into future prompt contexts within a strict token budget ($\le 10\%$), and provide Users with full memory management and GDPR export controls.

**Stakeholders:** User, LLM Service

---

## 15. Observability & Monitoring (Module M — Added Afterwards)

The system shall provide comprehensive tracing and automated evaluation across all LLM worker executions.

### FR-45 - Pipeline Observability & Named Span Tracing

**Requirement:**
The system shall instrument all generation nodes, chat turns, branch expansions, and research planner executions as named spans in LangSmith (`LANGCHAIN_TRACING_V2`), routing telemetry to dedicated development and production projects, and logging evaluation seed benchmarks automatically without impeding hermetic CI runs.

**Stakeholders:** Developer, LLM Service, Third Party API

---

## 16. Multimodal & Document Intake (Module N — Added Afterwards)

The system shall support modern document formats, web URLs, and explicit purpose routing during seed intake.

### FR-46 - Public URL Seed Intake with SSRF Protection

**Requirement:**
The system shall accept public web URLs as generation seeds, validate them against an SSRF protection blocklist (preventing loopback, private IP, and link-local traversal), and retrieve clean markdown text for mind map generation.

**Stakeholders:** User, Third Party API

### FR-47 - Office Document Ingestion & Map Purpose Routing

**Requirement:**
The system shall accept DOCX and PPTX file uploads, extract structural headings and slide titles, and allow the User to specify a generation purpose (`general`, `study`, `research`) that deterministically governs the activation of academic research tools (arXiv and Scholar).

**Stakeholders:** User, LLM Service

---

## 17. Functional Requirement–Stakeholder Mapping

The functional requirements can be mapped to their primary stakeholders as follows:

| Stakeholder | Functional Requirements |
| --- | --- |
| **User** | FR-01 to FR-07, FR-09 to FR-19, FR-42 to FR-44, FR-46 to FR-47 *(FR-20 to FR-25 descoped)* |
| **Admin** | FR-26, FR-27 *(Hardened with zero-SELECT RLS)* |
| **Developer** | FR-28 to FR-31, FR-45 |
| **QA / Tester** | FR-32 to FR-34 |
| **LLM Service** | FR-07, FR-08, FR-10, FR-15, FR-35 to FR-37, FR-42 to FR-45, FR-47 |
| **Third Party API** | FR-03, FR-08, FR-19, FR-38 to FR-41, FR-45, FR-46 |

