# Frontend Agent Instructions (`frontend/AGENT.md`)

This guide outlines rules, architectural constraints, and verification protocols that any AI coding assistant must review before making modifications to `frontend/`.

---

## 1. Scope & System Boundaries

- **Client Technology**: React 18 SPA built with Vite and `@xyflow/react` (React Flow).
- **Network Boundary**: All backend communication routes through `frontend/src/services/apiClient.js` leveraging Vite proxy configuration (`/api`, `/auth`, `/me`).
- **Sprint 1 Read-Only Canvas Boundary**: For Sprint 1 (US-01 through US-04), the canvas is strictly a presentation and exploration tool. Interactive drag repositioning, inline node CRUD, undo/redo stacks, and SVG/PNG vector exports are deferred to Sprint 2.

---

## 2. Non-Negotiable Invariants

### A. Dual-Defense Token Protocol & Storage Hygiene
- **Never Store JWTs in `localStorage`**:
  - `localStorage` is vulnerable to persistent XSS attacks. Access tokens are held in-memory and in active tab `sessionStorage` only.
  - Session longevity across tab restarts is anchored strictly by backend-managed `HttpOnly` cookies.
- **Silent Refresh Interceptor**: `apiClient.js` automatically captures `401 Unauthorized` responses and attempts a silent token refresh via `POST /auth/refresh` before failing.

### B. OAuth Return-Path Sanitization (Open Redirect Defense)
- Before redirecting to Google OAuth, `LoginPage.jsx` records the originating location in `sessionStorage.setItem('mindweave_oauth_return_to', path)`.
- Upon callback in `OAuthCallbackPage.jsx`, the return path **must** be validated:
  - Must begin with a single `/`.
  - Must reject protocol-relative URLs (`//evil.com`) and absolute schemes (`https://`).
  - Fall back safely to `/dashboard`.

### C. Regeneration Safety & Non-Destructive Cancellation (P0 Invariant)
- **Never Delete Existing Graphs on Regeneration Error**:
  - An empty draft creation (`initial_generate`) may safely delete its uncompleted placeholder if generation fails or is canceled.
  - Full map regeneration (`isRegeneration === true` or existing nodes count > 0) **must never** delete the mind map. On failure or cancellation, the existing graph must remain intact and the view restored to the editor.
- **Polled Job Ref Guard**:
  - In `GeneratingPage.jsx`, any job dispatched via `location.state.jobId` must be guarded by `polledJobIdRef` to prevent state transitions from triggering an infinite polling loop.
- **Explicit Editor Navigation on Completion**:
  - When `job.status === 'completed'`, `GeneratingPage` must navigate to `/editor/${targetMapId}` with `{ replace: true, state: { mode: 'editor' } }` to ensure URL and view state are synchronized.

### D. Topological Graph Fidelity
- React Flow adapters (`src/utils/graphAdapter.js`) must preserve Directed Acyclic Graph (DAG) cross-links without collapsing nodes into single-parent strict trees.
- Root node must render using `RootNode`, intermediate topics using `BranchNode`, and leaf insights using `ConceptNode`.

---

## 3. Environment & Execution Specifics

- **Working Directory**: Always set location to `frontend/`:
  ```powershell
  Set-Location "frontend"
  ```
- **Port Convention**: Vite dev server runs on `http://localhost:3000` (or `5173`) proxying requests to `http://localhost:8000`.

---

## 4. Verification Quality Checklist

Before finalizing any changes to `frontend/`, execute all four quality gates:

```powershell
# 1. Formatting check (Prettier)
npm run format:check

# 2. Linter check (ESLint - must report 0 errors)
npm run lint

# 3. Test suite (Vitest - must report 100% passed across all tests)
npm run test

# 4. Production build check (Vite build - must compile with zero errors)
npm run build
```

