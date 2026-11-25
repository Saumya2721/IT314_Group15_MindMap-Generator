# Frontend Agent Instructions (`frontend/AGENT.md`)

This guide outlines rules, architectural constraints, and verification protocols that any AI coding assistant must review before making modifications to `frontend/`.

---

## 1. Scope & System Boundaries

- **Client Technology**: React 18 SPA built with Vite and `@xyflow/react` (React Flow).
- **Network Boundary**: All backend communication routes through `frontend/src/services/apiClient.js` leveraging Vite proxy configuration (`/api`, `/auth`, `/me`).
- **Interactive Studio**: Supports full canvas interaction: node dragging with OCC coordinate saves, inline editing, node & edge CRUD, cycle-safe outline view, snapshot rollbacks, chat action proposals, and vector/raster exports.

---

## 2. Non-Negotiable Invariants

### A. Dual-Defense Token Protocol & PKCE OAuth Hygiene
- **Never Store JWTs in `localStorage`**:
  - `localStorage` is vulnerable to persistent XSS attacks. Access tokens are held in-memory and in active tab `sessionStorage` only.
  - Session longevity across tab restarts is anchored strictly by backend-managed `HttpOnly` cookies.
- **Silent Refresh Interceptor**: `apiClient.js` automatically captures `401 Unauthorized` responses and attempts a silent token refresh via `POST /auth/refresh` before failing.
- **PKCE Verifier Persistence**: `loginWithGoogle` must store `res.data.code_verifier` in `sessionStorage.setItem('mindweave_oauth_code_verifier', ...)` before redirecting, and `exchangeGoogleCode` must send it in the `POST /auth/google/callback` body.
- **Vite Proxy SPA Bypass**: `vite.config.js` proxy must bypass `/auth/callback` and serve `/index.html` to avoid 404 errors during OAuth redirects.

### B. OAuth Return-Path Sanitization (Open Redirect Defense)
- Before redirecting to Google OAuth, `LoginPage.jsx` records the originating location in `sessionStorage.setItem('mindweave_oauth_return_to', path)`.
- Upon callback in `OAuthCallbackPage.jsx`, the return path **must** be validated:
  - Must begin with a single `/`.
  - Must reject protocol-relative URLs (`//evil.com`) and absolute schemes (`https://`).
  - Fall back safely to `/dashboard`.

### C. Regeneration Safety & Non-Destructive Cancellation (P0 Invariant)
- **Never Delete Existing Graphs on Regeneration Error**:
  - An empty draft creation (`initial_generate`) may safely delete its uncompleted placeholder if generation fails or is canceled.
  - Full map regeneration or branch regeneration (`isRegeneration === true` or existing nodes count > 0) **must never** delete the mind map. On failure or cancellation, the existing graph must remain intact and the view restored to the editor.
- **Polled Job Ref Guard**:
  - In `GeneratingPage.jsx`, any job dispatched via `location.state.jobId` must be guarded by `polledJobIdRef` to prevent state transitions from triggering an infinite polling loop.
- **Explicit Editor Navigation on Completion**:
  - When `job.status === 'completed'`, `GeneratingPage` must navigate to `/editor/${targetMapId}` with `{ replace: true, state: { mode: 'editor' } }` to ensure URL and view state are synchronized.

### D. Topological Graph Fidelity & View Modes
- React Flow adapters (`src/utils/graphAdapter.js`) must preserve Directed Acyclic Graph (DAG) cross-links without collapsing nodes into single-parent strict trees.
- Root node must render using `RootNode`, intermediate topics using `BranchNode`, and leaf insights using `ConceptNode`.
- Visual Canvas mode and Outline mode (`OutlineView.jsx`) must stay in sync. Outline traversal must be cycle-safe and handle secondary roots for disconnected components.

### E. Optimistic Updates & Concurrency Control
- Graph autosave (`PATCH /api/maps/{id}/graph`) must pass `expected_version` to uphold OCC.
- Restoring a snapshot must update `mapVersionRef.current` with the new version returned by the server to avoid 409 conflicts on subsequent edits.
- Failed edge or node creation must immediately roll back optimistic canvas updates and display a user-facing toast alert.

### F. Layout & Flyout Positioning
- Floating inspector cards (`.node-inspector-flyout`, `.edge-inspector-flyout`) must be positioned at `top: 72px` or below with `max-height: calc(100vh - 96px)` to avoid being clipped behind the 56px editor navigation bar (`CanvasTopBar`).

### G. Admin Route Protection & Privacy Invariants (S3-FE1, S3-FE7)
- All `/admin/*` routes must be wrapped with an admin check (`user?.role === 'admin'`). Standard users attempting to access `/admin` must be redirected safely to `/dashboard` with an access-denied toast.
- The Admin UI must render aggregate telemetry and user accounts only, never displaying user mind map titles, node labels, or chat queries.

### H. Long-Term Memory Sovereignty & Export (S3-FE2)
- The `/memory` interface must allow users to view, toggle disabled state, delete, and export their own memories as a GDPR JSON bundle via `memoryService.exportMemories()`.
- No user can access or view another user's memory records.

### I. Multi-Modal Intake & SSRF Validation (S3-FE4, S3-FE5)
- `CreateMapPage.jsx` must support file uploads for DOCX, PPTX, PDF, and images, as well as `seed_url`.
- URL inputs must validate against private IP patterns (`127.0.0.1`, `10.*`, `192.168.*`, `172.16-31.*`, `localhost`) on the client side, displaying clear error messaging without submitting invalid targets.

### J. Top-to-Bottom Handle Alignment & React Flow Geometry
- Edge adapters in `src/utils/graphAdapter.js` must specify `sourceHandle: 'source-bottom'` and `targetHandle: 'target-top'`.
- All custom node components (`RootNode`, `BranchNode`, `ConceptNode`) must expose `<Handle id="source-bottom" position={Position.Bottom} />` and `<Handle id="target-top" position={Position.Top} />`.
- Fallback layouts must group nodes by depth level along $Y$ ($y = 80 + \text{depth} \times 200$) and space sibling nodes horizontally along $X$.

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

