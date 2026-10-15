"""ARQ Redis producer service for enqueuing mindmap generation jobs to the LLM worker."""
from __future__ import annotations

import logging
from typing import Any

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from core.config import settings

logger = logging.getLogger(__name__)


async def create_redis_pool(redis_url: str | None = None) -> ArqRedis | None:
    """Create and return an ArqRedis client pool.

    If Redis is unreachable, logs a warning and returns None so the
    backend can continue running with graceful fallbacks.
    """
    url = redis_url or settings.REDIS_URL
    try:
        pool = await create_pool(RedisSettings.from_dsn(url))
        logger.info("Successfully connected to Redis at %s", url)
        return pool
    except Exception as exc:
        logger.warning(
            "Could not connect to Redis at %s: %s. "
            "Generation requests will return 503 Service Unavailable until Redis is connected.",
            url,
            exc,
        )
        return None


async def close_redis_pool(pool: ArqRedis | None) -> None:
    """Safely close the Redis connection pool if open."""
    if pool is not None:
        try:
            await pool.close()
            logger.info("Redis connection pool closed.")
        except Exception as exc:
            logger.warning("Error closing Redis connection pool: %s", exc)


def get_redis_pool(app_or_request: Any) -> ArqRedis | None:
    """Retrieve the initialized Redis pool from app state."""
    if hasattr(app_or_request, "app"):
        return getattr(app_or_request.app.state, "redis_pool", None)
    state = getattr(app_or_request, "state", None)
    if state and hasattr(state, "redis_pool"):
        return state.redis_pool
    return getattr(app_or_request, "redis_pool", None)


async def enqueue_generation_job(pool: ArqRedis, request_payload: dict) -> str | None:
    """Enqueue a 'generate_mindmap' task on the ARQ queue with deterministic deduplication."""
    job_id_str = str(request_payload.get("job_id")) if request_payload.get("job_id") else None
    kwargs = {}
    if job_id_str:
        kwargs["_job_id"] = job_id_str

    job = await pool.enqueue_job("generate_mindmap", request_payload, **kwargs)
    if job:
        logger.info("Enqueued generate_mindmap task: arq_job_id=%s", job.job_id)
        return job.job_id
    return None
