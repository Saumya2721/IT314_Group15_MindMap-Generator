# Mind Map Generator from Raw Ideas

Group 15, IT314 Software Engineering. Users submit raw ideas or reference material (text, PDFs, images), an LLM pipeline turns them into a mind map of nodes and edges, and the user refines the result on an interactive canvas.

The repository is a foundation for the sprints in `Documentation/sprint_Grouping.md`. Product features are planned work and are not claimed as complete.

## Layout

| Folder | Purpose |
| --- | --- |
| `backend/` | FastAPI service (uv). |
| `llm/` | Seed processing, retrieval and generation logic with its own uv project. |
| `frontend/` | React app (Vite, TypeScript). |
| `supabase/` | Postgres migrations, storage setup and database tests. |
| `Documentation/` | Requirements, user stories, stakeholder analysis. |
| `docs/engineering/` | Code rules, testing strategy, configuration. |

## Quick start

Install [uv](https://docs.astral.sh/uv/), Node.js 22, Docker and the [Supabase CLI](https://supabase.com/docs/guides/local-development).

```bash
supabase start                # Postgres, auth and storage; applies supabase/migrations

cd backend
cp .env.example .env          # fill the keys from `supabase status`
uv sync
uv run uvicorn app.main:app --reload

cd frontend
cp .env.example .env
npm ci
npm run dev
```

`docker compose up --build` runs the backend and frontend in containers against the same local Supabase. The API health check is at `http://localhost:8000/health`.

## Checks

```bash
# backend/ and llm/
uv run ruff format --check . && uv run ruff check . && uv run mypy app && uv run pytest --cov=app
# in llm/, use `mypy mindmap_llm` and `--cov=mindmap_llm`

# frontend/
npm run format:check && npm run lint && npm run typecheck && npm run test && npm run build

# database (from the repo root)
supabase test db
```

For requirements, user stories, and design decisions, see `docs/requirements/` (formerly `Documentation/`). Read `CONTRIBUTING.md` before opening a pull request. `main` is protected: changes go through a pull request with one approval and a passing `ci-success` check.
