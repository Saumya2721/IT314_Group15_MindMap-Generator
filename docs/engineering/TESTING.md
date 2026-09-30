# Testing Strategy

Tests must be deterministic, isolated, and independent of order. Coverage is a signal, not a goal: do not write empty tests to raise it. The initial backend target is 80% for application code.

## What to test

- **Backend and `llm/`:** pytest for pure logic, API routes, and LLM-output validation. Test the success path, the important failures, timeouts, and malformed or duplicated LLM output. Mock the LLM, web search, arXiv, and export providers. CI must never call a real external service.
- **Frontend:** Vitest and Testing Library for components and hooks, with the API mocked.
- **Database:** pgTAP tests in `supabase/tests/database/` for rules the schema enforces: same-map integrity, the one-active-full-map-job rule, and Row Level Security isolation between users.

## Commands

```bash
# backend/ and llm/
uv sync --locked
uv run ruff format --check . && uv run ruff check .
uv run mypy app            # mypy mindmap_llm in llm/
uv run pytest --cov=app --cov-report=term-missing

# frontend/
npm ci && npm run format:check && npm run lint && npm run typecheck && npm run test && npm run build

# database
supabase start && supabase db reset && supabase test db
```

CI runs all of this from `.github/workflows/ci.yml`; a job only runs when its folder changed, and `ci-success` is the single required check.
