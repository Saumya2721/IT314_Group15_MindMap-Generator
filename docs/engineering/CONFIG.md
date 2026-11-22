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
| `REDIS_URL` | No | Redis connection URL for ARQ job queue and distributed rate limiting (S3-BE5). |
| `EXPORT_CONVERTER_URL` | No | Headless export microservice endpoint (`http://localhost:3001/convert`). |

## LLM (`llm/.env`)

| Variable | Secret | Purpose |
| --- | --- | --- |
| `LLM_PROVIDER` | No | Model provider, `openai`, `groq`, or `gemini`. |
| `LLM_MODEL` | No | Model name (e.g. `llama-3.3-70b-versatile`). |
| `LLM_API_KEY` | Yes | Provider API key. |
| `GEMINI_API_KEY` | Yes | Google Gemini Flash API key for vision extraction (S3-L6). |
| `EMBEDDING_MODEL` | No | Must stay the same for every embedding within one generation job. |
| `TAVILY_API_KEY` | Yes | Web search API key. |
| `REDIS_URL` | No | Redis connection for ARQ worker queue and research subgraph cache (S3-A2). |
| `LANGCHAIN_TRACING_V2` | No | LangSmith tracing toggle (`true`/`false`, defaults to `false` for hermetic CI). |
| `LANGCHAIN_API_KEY` | Yes | LangSmith API key for pipeline observability (S3-OBS). |
| `LANGCHAIN_PROJECT` | No | LangSmith project name (`mindweave-dev` or `mindweave-prod`). |
| `LANGCHAIN_ENDPOINT` | No | LangSmith API endpoint URL. |
| `RESEARCH_ENGINE` | No | Research execution mode (`subgraph` or `inline`, S3-A2). |
| `IMAGE_EXTRACTOR` | No | Primary image extractor (`gemini`, `openrouter`, or `tesseract`, S3-L6). |

Timeouts and retry limits have defaults in `llm/mindmap_llm/config.py`.

## Frontend (`frontend/.env`)

`VITE_API_BASE_URL`, `VITE_SUPABASE_URL` and `VITE_SUPABASE_ANON_KEY`. Only public values belong here, because Vite bundles them into the page.

## CI

CI uses a local Supabase started inside the job, never a hosted project, and needs no repository secrets. Add any future deployment secrets under Settings → Secrets and variables → Actions.
