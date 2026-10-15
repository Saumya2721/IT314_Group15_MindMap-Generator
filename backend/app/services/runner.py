"""Background execution and per-user generation rate limiting.

Extracted cleanly from mm_backend into backend.services.runner.
"""
from __future__ import annotations

import logging
import os
import time
from collections import defaultdict, deque
from uuid import UUID

from fastapi import HTTPException

from db.repository import ActiveJobAlreadyExists

try:
    from seed.normalizer import NoUsableSeedContent, SeedSubmission, normalize_seed
except ImportError:
    class NoUsableSeedContent(Exception):
        pass

    SeedSubmission = None
    normalize_seed = None

logger = logging.getLogger(__name__)

MAX_CONTEXT_CHARS = 12000
FAIL_MESSAGE = "Generation failed unexpectedly. Please try again."


class RateLimiter:
    """Sliding-window rate limiter for generation requests."""

    def __init__(self, max_calls: int, window_s: int):
        self.max_calls = max_calls
        self.window_s = window_s
        self._hits: dict[str, deque] = defaultdict(deque)

    def check(self, key: UUID | str) -> None:
        now = time.monotonic()
        q = self._hits[str(key)]
        while q and now - q[0] > self.window_s:
            q.popleft()
        if len(q) >= self.max_calls:
            wait = int(self.window_s - (now - q[0])) + 1
            raise HTTPException(
                429,
                "Too many generation requests. Please wait a bit and try again.",
                headers={"Retry-After": str(wait)},
            )
        q.append(now)


limiter = RateLimiter(int(os.getenv("GEN_RATE_LIMIT_PER_HOUR", "30")), 3600)


async def record_failure(pool, map_id: UUID, job_type: str) -> None:
    """Mark stuck pending/running jobs as failed so the UI displays errors and allows retries."""
    async with pool.acquire() as conn:
        status = await conn.execute(
            """
            UPDATE public.generation_jobs
               SET status = 'failed',
                   started_at = COALESCE(started_at, now()),
                   completed_at = now(),
                   progress_percent = LEAST(progress_percent, 99),
                   error_message = $2
             WHERE map_id = $1
               AND job_type IN ('generate', 'regenerate_full')
               AND status IN ('pending', 'running')
            """,
            map_id,
            FAIL_MESSAGE,
        )
        if status.endswith(" 0"):
            await conn.execute(
                """
                INSERT INTO public.generation_jobs
                    (map_id, job_type, status, progress_percent, error_message, started_at, completed_at)
                VALUES ($1, $2, 'failed', 0, $3, now(), now())
                """,
                map_id,
                job_type,
                FAIL_MESSAGE,
            )


async def _fail_quietly(pool, map_id: UUID, job_type: str) -> None:
    try:
        await record_failure(pool, map_id, job_type)
    except Exception:
        logger.exception("Could not record failure for map %s", map_id)


async def run_generate(app, *, map_id: UUID, user_id: UUID, submission, detail_level, job_id: UUID | None = None):
    try:
        service = getattr(app.state, "mindmap_service", getattr(app.state, "generation", None))
        if not service:
            raise RuntimeError("MindMapBackendService not initialized on app.state")

        topic = getattr(submission, "topic", None)
        ideas = getattr(submission, "ideas", None)
        context = None

        files = getattr(submission, "files", None)
        if files and normalize_seed and SeedSubmission:
            try:
                normalized = await normalize_seed(
                    submission=SeedSubmission(topic=None, ideas=None, files=files)
                )
                context = normalized.combined_text[:MAX_CONTEXT_CHARS]
            except NoUsableSeedContent:
                logger.warning("No usable text in uploads for map %s", map_id)

        await service.generate(
            map_id=map_id,
            user_id=user_id,
            topic=topic,
            ideas=ideas,
            detail_level=detail_level,
            context=context,
            existing_job_id=job_id,
        )
    except ActiveJobAlreadyExists:
        logger.warning("Generate skipped: active job already exists for map %s", map_id)
    except Exception:
        logger.exception("Generate crashed for map %s", map_id)
        await _fail_quietly(app.state.pool, map_id, "generate")


async def run_regenerate(app, *, map_id: UUID, user_id: UUID, instruction: str, detail_level, job_id: UUID | None = None):
    try:
        service = getattr(app.state, "mindmap_service", getattr(app.state, "generation", None))
        if not service:
            raise RuntimeError("MindMapBackendService not initialized on app.state")

        await service.regenerate_full(
            map_id=map_id,
            user_id=user_id,
            instruction=instruction,
            detail_level=detail_level,
            existing_job_id=job_id,
        )
    except ActiveJobAlreadyExists:
        logger.warning("Regenerate skipped: active job already exists for map %s", map_id)
    except Exception:
        logger.exception("Regenerate crashed for map %s", map_id)
        await _fail_quietly(app.state.pool, map_id, "regenerate_full")
