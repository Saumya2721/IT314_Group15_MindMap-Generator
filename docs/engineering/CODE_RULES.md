# Code Rules

Prefer no code over unnecessary code, and ten clear lines over fifty clever ones. Use a trusted library for a solved problem, but not for something trivial. Keep it simple, build only what is needed now, and abstract repeated logic only when it is really the same logic.

## General

Use meaningful names, small focused functions, explicit types, and clear input and output contracts. Validate early and handle errors explicitly; do not swallow exceptions. No hidden global state, unexplained magic numbers, dead or commented-out code, debug prints, secrets, or production URLs in commits.

Timestamps are UTC. Never log passwords, tokens, API keys, or private map and upload content.

## Python (`backend/`, `llm/`)

Ruff for formatting and linting, mypy in strict mode, Pydantic for request, response, configuration, and LLM-output validation. Use FastAPI dependency injection rather than module-level state, and keep routes thin. Use HTTPX with explicit timeouts for outbound calls. LLM and tool calls need a timeout and a bounded retry count.

## Database

Supabase Postgres is the source of truth. Schema changes are new files in `supabase/migrations/`; never edit an applied migration. Every table has Row Level Security enabled. The backend talks to Supabase through the Python client. Use the signed-in user's token for user data so RLS applies, and the service role key only for background work such as generation jobs. Anything that must succeed or fail as one unit goes into a Postgres function called with `.rpc()`, since the client cannot run multi-statement transactions.

## TypeScript and React

Strict TypeScript, small accessible components, semantic HTML, visible focus states, and meaningful error states. Use native `fetch` through one small API helper once the app talks to the backend.

## API

Product endpoints live under `/api/v1`; `/health` stays small. Return a documented error shape and validate input at the API boundary.
