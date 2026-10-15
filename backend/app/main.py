import logging
import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

# Backend configuration and database
# Backward-compatible exports
from auth.schemas import SignupRequest, UserCredentials  # noqa: F401

# LLM module and services
from config import get_settings as get_llm_settings
from core.config import settings as app_settings
from db.connection import create_db_pool
from dotenv import find_dotenv, load_dotenv
from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from maps.routes import router as maps_router
from providers.registry import get_llm_provider

# API routers
from routes.auth import router as auth_router
from routes.internal import router as internal_router
from routes.users import router as users_router
from services.generator import MindMapGenerator
from services.mindmap_service import MindMapBackendService
from services.queue import close_redis_pool, create_redis_pool

logger = logging.getLogger("backend.main")

# Configure Python import paths & load environment variables
backend_dir = Path(__file__).resolve().parent
project_dir = backend_dir.parent
llm_dir = project_dir / "llm"

# Search and load .env in all known locations
for env_candidate in (
    backend_dir / ".env",
    project_dir / ".env",
    llm_dir / ".env",
):
    if env_candidate.exists():
        load_dotenv(env_candidate)

load_dotenv(find_dotenv())

if llm_dir.exists() and str(llm_dir) not in sys.path:
    sys.path.insert(0, str(llm_dir))

if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))


FRONTEND_URL = app_settings.FRONTEND_URL


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Initialize the database pool, generator, and application state."""
    pool = None
    try:
        pool = await create_db_pool()
        logger.info("Database connection pool created successfully.")
    except Exception as exc:
        logger.warning(
            "Database pool creation failed or DATABASE_URL not set: %s. "
            "Database-dependent routes will return 503 until DATABASE_URL is configured.",
            exc,
        )

    # Build the generator and backend service
    llm_settings = get_llm_settings()
    generator = None
    try:
        generator = MindMapGenerator(
            llm=get_llm_provider(llm_settings),
            max_tokens=llm_settings.llm.max_output_tokens,
            max_retries=llm_settings.generation.max_generation_retries,
        )
    except Exception as exc:
        logger.warning("Could not initialize default LLM provider: %s", exc)

    service = MindMapBackendService(pool, generator)

    redis_pool = await create_redis_pool()

    app.state.pool = pool
    app.state.redis_pool = redis_pool
    app.state.llm_generator = generator
    app.state.mindmap_service = service
    app.state.generation = service

    try:
        yield
    finally:
        if redis_pool is not None:
            await close_redis_pool(redis_pool)
        if pool is not None:
            await pool.close()


app = FastAPI(
    title=app_settings.APP_NAME,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=app_settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def custom_openapi() -> dict[str, Any]:
    if app.openapi_schema:
        return app.openapi_schema

    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )

    schemas = openapi_schema.get("components", {}).get("schemas", {})

    for schema in schemas.values():
        if not isinstance(schema, dict):
            continue

        props = schema.get("properties", {})
        if "files" in props:
            props["files"] = {
                "type": "array",
                "items": {"type": "string", "format": "binary"},
                "title": "Files",
                "description": "Upload one or multiple files (PDF, PNG, JPG, WEBP)",
            }
        if "file" in props:
            props["file"] = {
                "type": "string",
                "format": "binary",
                "title": "File",
                "description": "Upload a single PDF or image",
            }

    app.openapi_schema = openapi_schema
    return app.openapi_schema


app.openapi = custom_openapi

app.openapi = custom_openapi

# Register routers with clean, canonical docs
app.include_router(auth_router, prefix="/api")
app.include_router(auth_router, include_in_schema=False)
app.include_router(users_router, prefix="/api/users")
app.include_router(users_router, include_in_schema=False)
app.include_router(maps_router)
app.include_router(internal_router)


@app.get("/", include_in_schema=False)
@app.get("/app", include_in_schema=False)
def root() -> dict[str, str]:
    return {"message": "Welcome to Mind Map Generator API"}


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "Mind Map Generator API",
        "db_connected": getattr(app.state, "pool", None) is not None,
    }


@app.get("/api/health/ready")
async def health_readiness(response: Response):
    pool = getattr(app.state, "pool", None)
    redis_pool = getattr(app.state, "redis_pool", None)

    db_ok = False
    if pool is not None:
        try:
            await pool.fetchval("SELECT 1")
            db_ok = True
        except Exception:
            db_ok = False

    redis_ok = False
    if redis_pool is not None:
        try:
            await redis_pool.ping()
            redis_ok = True
        except Exception:
            redis_ok = False

    # Full system readiness requires both PostgreSQL and Redis
    is_ready = db_ok and redis_ok
    if not is_ready:
        response.status_code = 503

    return {
        "status": "ready" if is_ready else "not_ready",
        "service": "Mind Map Generator API",
        "database": "connected" if db_ok else "disconnected",
        "redis": "connected" if redis_ok else "disconnected",
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
