import collections
from datetime import datetime, timedelta

from fastapi import HTTPException, Request

# Sliding-window rate limiter (in-memory per client/user)
request_history = collections.defaultdict(list)
MAX_REQUESTS_PER_MINUTE = 20


def check_rate_limit(request: Request):
    """Rate limit per user token if present, falling back to client IP."""
    token = request.headers.get("authorization") or request.cookies.get("access_token")
    client_key = token[-32:] if token else (request.client.host if request.client else "unknown_ip")
    now = datetime.now()

    time_window = now - timedelta(minutes=1)

    # Filter out requests older than the time window
    request_history[client_key] = [req_time for req_time in request_history[client_key] if req_time > time_window]

    if len(request_history[client_key]) >= MAX_REQUESTS_PER_MINUTE:
        raise HTTPException(
            status_code=429,
            detail="Too Many Requests. Please wait a minute before sending more chat messages.",
        )

    request_history[client_key].append(now)


def get_mindmap_service(request: Request):
    """Retrieve the MindMapBackendService instance initialized on FastAPI app.state."""
    service = getattr(request.app.state, "mindmap_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="Mindmap service is currently unavailable.")
    return service
