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

### Sprint Outcome

At the end of this sprint, a user should be able to manually edit the generated mind map, refine individual branches, save the map, search and reopen previously saved maps, and export the current map as PDF or PNG.

---

## Sprint 3 – Platform Administration, Developer Configuration and QA Enablement

### Goal

Add the administrative capabilities required to manage the platform while protecting user privacy, and provide developers and QA with a configurable and testable generation pipeline.

### User Stories

- **US-09 – Admin Oversight**
  - Provide an admin panel.
  - Show aggregate platform usage information.
  - Allow supported user account management.
  - Restrict admin functionality to authorized users.
  - Prevent unnecessary access to private user map content.
  - Record administrative actions for auditing.

- **US-10 – Configure the Generation Pipeline**
  - Configure the LLM integration.
  - Add or update research tools.
  - Keep LLM and tool integrations separate from core generation logic.
  - Handle tool failures and configuration errors.
  - Keep API keys and other secrets outside the source code.

- **US-11 – Test in Isolation**
  - Provide a non-production test environment.
  - Mock LLM and third-party service responses.
  - Run repeatable tests.
  - Record test results and useful error logs.
  - Include regression, authorization, privacy, generation, and editing tests.

### Sprint Outcome

At the end of this sprint, authorized administrators should be able to manage supported account operations and view platform-level usage information without exposing private user content. Developers should be able to configure and extend the generation pipeline more easily, while QA should be able to test the application in an isolated environment without depending completely on live external services.

---

# Notes

- **US-01 to US-04** are grouped into the first sprint because authentication and seed submission are the starting points of the main user flow, while mind map generation and whole-map regeneration directly depend on the seed being available.
- **US-05 to US-08** remain together in the second sprint because they all operate on the generated mind map. This sprint focuses on editing, refinement, persistence, search, reopening, and export.
- **US-09 to US-11** are grouped into the third sprint because administrative functionality, generation-pipeline configuration, and isolated QA/testing are primarily concerned with platform management, maintainability, security, and operational readiness.
