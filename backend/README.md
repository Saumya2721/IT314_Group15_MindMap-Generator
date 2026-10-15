# Mind Map Generator — Merged Project

This repository unifies the FastAPI backend with the LLM generation engine and decoupled worker.

## Repository Structure

```text
mindmap_generator/
├── backend/                  # FastAPI Application
│   ├── main.py               # Main application entry point & router registration
│   ├── auth/                 # Supabase authentication service & schemas
│   ├── core/                 # App configuration, security, database & Supabase client
│   │   ├── config.py         # Application settings
│   │   ├── database.py       # Pool alias
│   │   ├── security.py       # Cookie management
│   │   └── supabase.py       # Supabase Admin / Anon client & auth dependencies
│   ├── db/                   # Database layer (PostgreSQL / Supabase asyncpg)
│   │   ├── connection.py     # Connection pool & transaction manager
│   │   ├── models.py         # Database model dataclasses
│   │   └── repository.py     # Centralized SQL repository queries
│   ├── maps/                 # Mind map routes (/api/maps, quick-generate, etc.)
│   ├── uploads/              # File upload validation & Supabase Storage
│   ├── services/             # Core business logic services
│   │   ├── generator.py      # MindMapGenerator bridge to LLM engine
│   │   ├── mindmap_service.py# MindMapBackendService (DB transactions + LLM call)
│   │   └── runner.py         # Background execution & rate limiter
│   ├── routes/               # API routers (auth, users, maps, internal callbacks)
│   │   └── internal.py       # Worker callback endpoints (/internal/jobs/...)
│   ├── static/               # Interactive Mind Map Studio web UI (index.html)
│   └── tests/                # Backend unit & integration tests
│
├── llm/                      # LLM Engine & Decoupled ARQ Worker
│   ├── config.py             # LLM, queue, RAG, and provider configurations
│   ├── generator.py          # MindMapGenerator implementation
│   ├── contracts/            # Wire contracts (GenerationRequest, GenerationResult, drafts)
│   ├── providers/            # LLM provider adapters (Groq, Jev, fallback chain)
│   ├── orchestration/        # LangGraph state machine, nodes, and prompts
│   ├── validation/           # Schema validation rules (DR-01 to DR-04, single root, DAG)
│   ├── seed/                 # Document normalization (PDF text, image OCR)
│   ├── rag/                  # Pinecone hybrid vector store & retrieval
│   ├── callback/             # HTTP callback client for decoupled worker
│   ├── worker/               # ARQ background worker tasks
│   └── tests/                # LLM test suite
│
└── requirements.txt          # Combined project dependencies
```

## Setup & Running

1. **Activate or create your virtual environment**:
   ```bash
   cd backend
   .\venv\Scripts\activate   # on Windows
   # or source venv/bin/activate on Linux/Mac
   ```

2. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure Environment Variables & Database**:
   - Copy `backend/.env.example` to `backend/.env` (and `llm/.env.example` to `llm/.env` if running the ARQ worker separately).
   - Fill in your `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `DATABASE_URL`, and `LLM_API_KEY` (Groq).
   - Set up your Supabase database tables by running the script at [backend/db/schema.sql](file:///c:/Users/LENOVO/Downloads/mindmap_generator/mindmap_generator/backend/db/schema.sql) in your Supabase SQL Editor.

4. **Run the FastAPI server**:
   ```bash
   cd backend
   python main.py
   # or: uvicorn main:app --reload
   ```

5. **Access the application**:
   - Web Studio UI: [http://localhost:8000/app](http://localhost:8000/app) or [http://localhost:8000/](http://localhost:8000/)
   - Swagger OpenAPI Docs: [http://localhost:8000/docs](http://localhost:8000/docs)
   - Health Check: [http://localhost:8000/api/health](http://localhost:8000/api/health)

## Running Tests

Run the full test suite (backend + LLM unit tests):
```bash
cd backend
pytest tests ..\llm\tests
```
