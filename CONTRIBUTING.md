# Contributing

This is a team course project. Keep each change small, tested, and linked to a story or requirement.

## Workflow

`main` is protected. Nobody pushes to it directly, and you cannot approve your own pull request.

1. Branch from `main` (`feature/us-03-generate-map`, `fix/export-crash`).
2. Make small commits. Conventional Commit style is preferred: `feat:`, `fix:`, `test:`, `docs:`, `refactor:`, `chore:`, `ci:`.
3. Push and open a pull request. Fill in the template, including the story or requirement IDs.
4. Wait for `ci-success` to pass and for one teammate to approve. New commits clear existing approvals.
5. Squash-merge. The branch is deleted automatically.

## Checks before you push

Backend and `llm/` (from that folder): `uv sync --locked`, `uv run ruff format --check .`, `uv run ruff check .`, `uv run mypy <package>`, `uv run pytest --cov`. The package is `app` for `backend/` and `mindmap_llm` for `llm/`.

Frontend: `npm ci`, `npm run format:check`, `npm run lint`, `npm run typecheck`, `npm run test`, `npm run build`.

Database: `supabase start`, `supabase db reset`, `supabase test db`.

Run `docker compose config` after changing `compose.yaml`.

## Database changes

Never edit a migration that is already on `main`. Add a new one with `supabase migration new <name>` and cover any new rule with a pgTAP test in `supabase/tests/database/`.

## Secrets

Real keys never go in the repository. Use `.env` files locally (ignored by git) and GitHub Secrets for CI.
