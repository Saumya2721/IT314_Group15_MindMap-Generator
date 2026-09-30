# Configuration

`.env.example` files list names and safe example values only. Real keys never go in git.

## Backend (`backend/.env`)

| Variable | Secret | Purpose |
| --- | --- | --- |
| `APP_ENV` | No | Environment name, `development` by default. |
| `CORS_ORIGINS` | No | Comma-separated browser origins, `http://localhost:5173` by default. |
| `SUPABASE_URL` | No | Supabase API URL. Local value comes from `supabase status`. |
| `SUPABASE_ANON_KEY` | No | Public key, subject to Row Level Security. |
| `SUPABASE_SERVICE_ROLE_KEY` | Yes | Bypasses Row Level Security. Server only; never in the frontend, logs, or a commit. |

## LLM (`llm/.env`)

| Variable | Secret | Purpose |
| --- | --- | --- |
| `LLM_PROVIDER` | No | Model provider, `openai` by default. |
| `LLM_MODEL` | No | Model name. |
| `LLM_API_KEY` | Yes | Provider API key. |
| `EMBEDDING_MODEL` | No | Must stay the same for every embedding within one generation job. |
| `TAVILY_API_KEY` | Yes | Web search key. |

Timeouts and retry limits have defaults in `llm/mindmap_llm/config.py`.

## Frontend (`frontend/.env`)

`VITE_API_BASE_URL`, `VITE_SUPABASE_URL` and `VITE_SUPABASE_ANON_KEY`. Only public values belong here, because Vite bundles them into the page.

## CI

CI uses a local Supabase started inside the job, never a hosted project, and needs no repository secrets. Add any future deployment secrets under Settings → Secrets and variables → Actions.
