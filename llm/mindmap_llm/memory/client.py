"""
Backend HTTP client for User Memory operations (S3-L3).

Interacts with backend S3-BE4 endpoints:
- GET  /internal/memory: Retrieves active user memories.
- POST /internal/memory/propose: Submits worker memory proposals.

Enforces:
- Graceful degradation (network failures never crash generation or chat).
- Timeout protection.
- Proper Bearer auth token forwarding.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

import httpx
from memory.schemas import MemoryProposalResponse, UserMemory

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 5.0


class MemoryClient:
    """Asynchronous client for memory retrieval and proposal against backend."""

    def __init__(
        self,
        base_url: str,
        token: str,
        timeout_s: float = _DEFAULT_TIMEOUT,
        internal_service_key: str | None = None,
        job_id: UUID | None = None,
    ) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.token = token
        import os

        key = internal_service_key or os.environ.get("INTERNAL_SERVICE_KEY", "")
        headers = {
            "Content-Type": "application/json",
            "X-Internal-Service-Key": key,
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if job_id:
            headers["X-Internal-Job-ID"] = str(job_id)
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers=headers,
            timeout=timeout_s,
        )

    async def retrieve_user_memories(
        self,
        user_id: UUID | str,
        map_id: UUID | str | None = None,
        limit: int = 50,
    ) -> list[UserMemory]:
        """Fetch active memories for a given user from the backend.

        Gracefully degrades to an empty list on connection/HTTP failure.
        """
        if not self.base_url:
            return []

        params: dict[str, Any] = {
            "user_id": str(user_id),
            "is_active": "true",
            "limit": limit,
        }
        if map_id:
            params["map_id"] = str(map_id)

        try:
            # Try internal memory endpoint first, fall back to /api/internal/memory
            resp = await self._client.get("/internal/memory", params=params)
            if resp.status_code == 404:
                resp = await self._client.get("/api/internal/memory", params=params)

            if resp.status_code == 200:
                data = resp.json()
                items = data.get("items") or data.get("memories") or []
                memories: list[UserMemory] = []
                for item in items:
                    try:
                        if "importance" in item and "importance_score" not in item:
                            item["importance_score"] = item["importance"]
                        if "memory_type" in item and "kind" not in item:
                            raw_k = item["memory_type"]
                            item["kind"] = raw_k if raw_k in ("preference", "decision", "fact") else "fact"
                        memories.append(UserMemory.model_validate(item))
                    except Exception as parse_err:
                        logger.debug("Skipping unparseable memory item: %s", type(parse_err).__name__)
                return memories

            logger.warning(
                "Memory retrieval for user %s returned status %d: %s",
                user_id,
                resp.status_code,
                resp.text[:150],
            )
            return []
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            logger.warning("Memory retrieval endpoint unreachable (%s): %s", self.base_url, exc)
            return []
        except Exception as exc:
            logger.warning("Unexpected error during memory retrieval: %s", exc)
            return []

    async def propose_memory(
        self,
        user_id: UUID | str,
        content: str,
        memory_type: str = "preference",
        map_id: UUID | str | None = None,
        kind: str | None = None,
        importance: float = 1.0,
    ) -> MemoryProposalResponse:
        """Submit a single memory proposal to POST /internal/memory/propose.

        Gracefully handles errors without raising.
        """
        if not self.base_url:
            return MemoryProposalResponse(stored=False, reason="no_backend_url")

        clean_kind = kind or (memory_type if memory_type in ("preference", "decision", "fact") else "fact")
        payload: dict[str, Any] = {
            "user_id": str(user_id),
            "content": content,
            "kind": clean_kind,
            "memory_type": memory_type,
            "importance": float(importance),
        }
        if map_id:
            payload["map_id"] = str(map_id)

        try:
            resp = await self._client.post("/internal/memory/propose", json=payload)
            if resp.status_code == 404:
                resp = await self._client.post("/api/internal/memory/propose", json=payload)

            if resp.status_code in (200, 201):
                data = resp.json()
                return MemoryProposalResponse.model_validate(data)

            logger.warning(
                "Memory proposal for user %s returned status %d: %s",
                user_id,
                resp.status_code,
                resp.text[:150],
            )
            return MemoryProposalResponse(stored=False, reason=f"http_{resp.status_code}")
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            logger.warning("Memory proposal endpoint unreachable (%s): %s", self.base_url, exc)
            return MemoryProposalResponse(stored=False, reason="transport_error")
        except Exception as exc:
            logger.warning("Unexpected error during memory proposal: %s", exc)
            return MemoryProposalResponse(stored=False, reason=str(exc))

    async def aclose(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()
