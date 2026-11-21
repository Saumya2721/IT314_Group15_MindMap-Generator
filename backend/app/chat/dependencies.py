from __future__ import annotations

from fastapi import HTTPException, Request
from services.queue import get_redis_pool
from services.rate_limiter import extract_client_key, rate_limiter

# Deprecated in-memory tracker kept for backwards-compatibility inspection if needed
request_history: dict = {}
MAX_REQUESTS_PER_MINUTE = 20


async def check_rate_limit(request: Request) -> None:
    """Rate limit chat messages using distributed Redis-backed sliding window (S3-BE5 / FR-41)."""
    redis_pool = get_redis_pool(request)
    client_key = extract_client_key(request)
    await rate_limiter.check(client_key, api="chat", redis=redis_pool)


def get_mindmap_service(request: Request):
    """Retrieve the MindMapBackendService instance initialized on FastAPI app.state."""
    service = getattr(request.app.state, "mindmap_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="Mindmap service is currently unavailable.")
    return service
