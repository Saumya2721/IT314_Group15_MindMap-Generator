# MINDWEAVE — Cartography of Thought (Frontend)

A modern, topological visual workspace that transforms raw, unstructured thoughts into interactive knowledge graphs. Built with React, Vite, and a custom Warm Editorial Studio design system, powered by `@xyflow/react` (React Flow) and backed by FastAPI, Supabase, and ARQ queue workers.

---

## 1. Project Overview

MINDWEAVE is an intellectual atelier designed to help researchers, thinkers, and builders organize complex ideas into structured taxonomy. The frontend combines:
- **Dual-Defense Authentication & Security**: Session persistence anchored by secure `HttpOnly` cookies combined with tab-scoped in-memory tokens.
- **Curated Archive Dashboard**: Real-time listing, search, sorting, and management of user-owned mind maps directly from PostgreSQL.
- **Multi-Modal Synthesis Ingestion**: Thought capture studio supporting topics, stream-of-consciousness raw notes, and academic document/image uploads (`.pdf`, `.png`, `.jpg`, `.txt`).
- **Truthful Generation Pipeline**: Dynamic progress tracking powered by asynchronous ARQ background workers with bounded backoff polling.
- **Topological Knowledge Graph**: Visual graph presentation powered by `@xyflow/react` rendering rooted DAGs with cross-links.

---

## 2. Authentication & Security Architecture

MINDWEAVE implements an enterprise **Dual-Defense Architecture**:

```
 ┌─────────────────────────────────────────────────────────────┐
 │                       Browser Client                        │
 │                                                             │
 │  ┌───────────────────────┐       ┌───────────────────────┐  │
 │  │      HttpOnly         │       │  In-Memory /          │  │
 │  │      Cookies          │       │  sessionStorage       │  │
 │  │  (access_token,       │       │  (active tab token,   │  │
 │  │   refresh_token)      │       │   auth user object)   │  │
 │  └──────────┬────────────┘       └───────────┬───────────┘  │
 └─────────────┼────────────────────────────────┼──────────────┘
               │ credentials: 'include'         │ Authorization: Bearer <token>
               ▼                                ▼
 ┌─────────────────────────────────────────────────────────────┐
 │                      FastAPI Backend                        │
 │                                                             │
 │  • get_current_user: verifies Bearer header or cookie       │
 │  • /auth/refresh: exchanges refresh cookie for fresh token  │
 │  • /auth/logout: clears HttpOnly cookies                    │
 └─────────────────────────────────────────────────────────────┘
```

1. **Persistent Anchor (HttpOnly Cookies)**:
   - When logging in or refreshing tokens, the backend issues secure, `HttpOnly` `access_token` and `refresh_token` cookies.
   - These cookies cannot be read or stolen by JavaScript (XSS immune).
   - If a user closes the browser tab and returns later, opening the app automatically invokes `GET /me` with `credentials: 'include'`. The cookie restores the authenticated session seamlessly.
2. **Tab-Scoped Bearer Authorization (`sessionStorage`)**:
   - The active tab retains the access token in-memory / `sessionStorage` for appending `Authorization: Bearer <token>` headers to API requests.
   - When the tab is closed, `sessionStorage` is automatically wiped by the browser.
3. **Silent Token Refresh**:
   - If an API request encounters `401 Unauthorized`, `apiClient` automatically sends a refresh request to `POST /auth/refresh` using the HttpOnly refresh token cookie. Upon receiving fresh tokens, it retries the original request seamlessly.
4. **No Permanent `localStorage` Token Retention**:
   - Raw JWT tokens are never written to `localStorage`, avoiding long-term vulnerability to script-injection attacks.

---

## 3. Sprint Scope & Canvas Engine

### Sprint 1 Implemented Capabilities
- **Seed Submission & Generation**: Unified 2-step pipeline: `POST /api/maps` followed by `POST /api/maps/{id}/generate` with multipart support for text and document uploads.
- **Truthful Progress Feedback**: Bounded exponential backoff polling (`1s` - `2.5s`) tracking real ARQ worker milestones (0% to 100%).
- **Leave Generation / Run in Background**: Users can leave the synthesis screen anytime; worker processing continues asynchronously and results appear on the dashboard.
- **React Flow Display Engine**: Fully migrated to `@xyflow/react` (`^12.4.4`) in read-only presentation mode:
  - Preserves DAG cross-links 1:1 without collapsing into strict single-parent trees (DR-02 compliance).
  - Custom nodes: `RootNode` (central theme), `BranchNode` (thematic groupings), `ConceptNode` (detailed concepts).
  - Native `<Background />`, `<Controls />`, and `<MiniMap />` navigation.
  - Contextual `NodeInspector` flyout displaying concept label, description, and direct connection metrics.

### Sprint 2 Planned Roadmap (Explicitly Deferred)
- Direct canvas node dragging and Cartesian coordinate persistence (`PUT /api/maps/{id}/graph`).
- Interactive node mutation (inline text editing, node addition/deletion, reparenting).
- Node-level partial regeneration (`POST /api/maps/{id}/nodes/{node_id}/regenerate`).
- Undo/redo state history stack.
- Multi-format vector exports (PNG, SVG, Markdown outline).

---

## 4. Technologies Used

- **UI Framework:** React 18 (`^18.3.1`) & React DOM (`^18.3.1`)
- **Build Tool & Dev Server:** Vite 6 (`^6.2.0`) with `@vitejs/plugin-react` (`^4.3.4`)
- **Routing:** React Router DOM v6 (`^6.29.0`)
- **Canvas & Graph Engine:** `@xyflow/react` (`^12.4.4`) (React Flow)
- **Styling:** Modular CSS with CSS custom properties adhering to the Warm Editorial Studio design system
- **Typography:** Google Fonts (*Cinzel*, *Newsreader*, and *Plus Jakarta Sans*)
- **Iconography:** Google Material Symbols Outlined

---

## 5. Project Folder Structure

```text
frontend/
├── .env                              # Local environment variables
├── .env.example                      # Environment variables template
├── index.html                        # Application HTML shell
├── package.json                      # Dependencies and scripts
├── vite.config.js                    # Vite dev proxy configuration (/api, /auth, /me)
├── dist/                             # Production build output
└── src/
    ├── main.jsx                      # App entry point
    ├── App.jsx                       # Routing & ProtectedRoute boundaries
    ├── styles/
    │   └── index.css                 # Global tokens, typography, and palette
    ├── services/
    │   ├── apiClient.js              # Modular fetch client with auto-refresh & credentials
    │   ├── authService.js            # Live auth endpoints (/auth/login, /signup, /me, etc.)
    │   └── mindMapService.js         # Mind map CRUD, generation, polling, and graph APIs
    ├── utils/
    │   ├── formatters.js             # Date/time, relative formats, avatar utilities
    │   ├── graphAdapter.js           # Backend DAG to React Flow format adapter
    │   └── layout.js                 # Fallback topological coordinates calculator
    ├── context/
    │   ├── AuthContext.jsx           # Global user authentication & cookie hydration
    │   └── MindMapContext.jsx        # Map state, drafts, and bookmarks context
    └── components/
        ├── layout/
        │   ├── AppHeader.jsx         # Sticky application header & profile menu
        │   └── AppFooter.jsx         # Application footer
        ├── dashboard/
        │   ├── MindMapCard.jsx       # Mind map card with database metadata
        │   └── MindMapCard.css       # Card styling
        ├── generator/
        │   ├── ArchivalStepper.jsx   # Truthful 4-stage pipeline stepper
        │   └── ArchivalStepper.css   # Progress bar styling
        └── canvas/
            ├── MindMapCanvas.jsx     # @xyflow/react canvas viewport
            ├── MindMapCanvas.css     # Canvas container and background styling
            ├── CanvasTopBar.jsx      # Top bar with title, export, and regenerate triggers
            ├── NodeInspector.jsx     # Read-only node inspection drawer
            └── nodes/
                ├── RootNode.jsx      # Central origin node
                ├── BranchNode.jsx    # Primary thematic branch node
                ├── ConceptNode.jsx   # Granular concept node
                └── nodes.css         # Node typography, borders, and handles
```

---

## 6. Running Locally

### 1. Install Dependencies
```bash
cd frontend
npm install
```

### 2. Configure Environment
Copy `.env.example` to `.env`:
```bash
VITE_API_URL=
```
*(In development, leave `VITE_API_URL` empty so requests route through the Vite proxy in `vite.config.js` to `http://localhost:8000`).*

### 3. Start Development Server
```bash
npm run dev
```
The application will be accessible at `http://localhost:3000`.

### 4. Build for Production
```bash
npm run build
```
The output will be generated in `dist/`.
