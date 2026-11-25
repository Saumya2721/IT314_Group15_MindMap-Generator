"""
Replaces jobs/lifecycle.py::JobTracker entirely. The worker no longer
owns generation_jobs — the backend does — so instead of writing SQL
guarded by an in-process state machine, this client POSTs to internal
backend endpoints and guards against the one failure mode that matters
on THIS side: calling complete()/fail() more than once for the same job
(e.g. if a queue retries a task after a late callback already landed).

The backend is responsible for its own idempotency on receipt (a
duplicate POST for an already-`completed` job should be a no-op, not an
error) — this client's guard is a courtesy, not the source of truth.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from uuid import UUID

import httpx
from arq import Retry
from arq.connections import ArqRedis
from contracts.models import ChatCompleteCallback, GenerationResult

logger = logging.getLogger(__name__)


class CallbackAlreadySent(Exception):
    """Raised if progress()/complete()/fail() is called again after a
    terminal callback (complete/fail) already went out for this job."""


class JobCallbackClient:
    """One instance per job (built per task invocation, not shared).

    `base_url` and `token` come from the GenerationRequest — the backend
    mints a short-lived, job-scoped token when it enqueues the task, so
    a worker can only report on the one job it was given, not any other.
    """

    def __init__(self, *, job_id, base_url: str, token: str, timeout_s: float = 10.0, redis: ArqRedis | None = None):
        self._job_id = job_id
        self._base_url = base_url
        self._token = token
        self._redis = redis
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            timeout=timeout_s,
        )
        self._terminal_sent = False

    async def check_status(self) -> dict:
        """Pre-flight check to query whether backend already completed or terminated this job."""
        try:
            resp = await self._client.get(f"/internal/jobs/{self._job_id}")
            if resp.status_code == 200:
                return resp.json()
        except Exception as exc:
            logger.debug("job %s: pre-flight check_status failed: %s", self._job_id, exc)
        return {}

    async def progress(self, percent: int) -> None:
        if self._terminal_sent:
            raise CallbackAlreadySent(f"job {self._job_id}: progress() called after a terminal callback")
        try:
            resp = await self._client.post(
                f"/internal/jobs/{self._job_id}/progress", json={"progress_percent": percent}
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            # A missed progress tick is not fatal to the generation itself —
            # log it and keep going rather than failing the whole job over
            # a UI nicety. complete()/fail() below are NOT allowed to do this.
            logger.warning("job %s: progress callback failed (percent=%d): %s", self._job_id, percent, exc)

    async def _send_result(self, path: str, payload: str, status: str) -> dict:
        if self._terminal_sent:
            raise CallbackAlreadySent(f"job {self._job_id}: terminal result already submitted")
        self._terminal_sent = True
        if self._redis is not None:
            try:
                await self._redis.enqueue_job(
                    "replay_terminal_callback",
                    str(self._job_id),
                    self._base_url,
                    self._token,
                    path,
                    payload,
                    status,
                    _job_id=f"terminal:{self._job_id}",
                    _defer_by=20,
                )
            except Exception:
                logger.exception("job %s: could not persist callback retry", self._job_id)
        return await self._post_result(path, payload, status)

    async def _post_result(self, path: str, payload: str, status: str) -> dict:
        for attempt in range(3):
            try:
                resp = await self._client.post(path, content=payload)
                resp.raise_for_status()
                return resp.json() if resp.content else {"status": status}
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                if attempt == 2 or (isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code < 500):
                    raise
                await asyncio.sleep(0.5 * 2**attempt)
        raise RuntimeError("Callback attempts exhausted")

    async def complete(self, result: GenerationResult) -> dict:
        return await self._send_result(f"/internal/jobs/{self._job_id}/complete", result.model_dump_json(), "completed")

    async def fail(self, result: GenerationResult) -> dict:
        return await self._send_result(f"/internal/jobs/{self._job_id}/fail", result.model_dump_json(), "failed")

    async def complete_chat(
        self,
        *,
        map_id: UUID,
        assistant_message: str,
        summary: str | None = None,
        action_proposal: dict[str, Any] | None = None,
        cited_node_ids: list[UUID] | None = None,
        source_refs: list[str] | None = None,
    ) -> dict:
        payload = ChatCompleteCallback(
            map_id=map_id,
            job_id=UUID(str(self._job_id)),
            assistant_message=assistant_message,
            summary=summary,
            action_proposal=action_proposal,
            cited_node_ids=cited_node_ids or [],
            source_refs=source_refs or [],
            status="completed",
        )
        return await self._send_result(
            f"/internal/chat/{self._job_id}/complete", payload.model_dump_json(), "completed"
        )

    async def fail_chat(self, *, map_id: UUID, error: str) -> dict:
        payload = ChatCompleteCallback(
            map_id=map_id,
            job_id=UUID(str(self._job_id)),
            assistant_message="",
            status="failed",
            error=error,
        )
        return await self._send_result(f"/internal/chat/{self._job_id}/complete", payload.model_dump_json(), "failed")

    async def aclose(self) -> None:
        await self._client.aclose()


async def replay_terminal_callback(
    ctx: dict, job_id: str, base_url: str, token: str, path: str, payload: str, status: str
) -> None:
    client = JobCallbackClient(job_id=job_id, base_url=base_url, token=token)
    try:
        receipt = await client.check_status()
        if not receipt.get("already_terminal"):
            await client._post_result(path, payload, status)
    except (httpx.TransportError, httpx.HTTPStatusError) as exc:
        if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code < 500:
            raise
        raise Retry(defer=min(300, 5 * 2 ** ctx.get("job_try", 1))) from exc
    finally:
        await client.aclose()


class NullCallbackClient:
    """No-op stand-in for local/manual runs (scripts/run_local.py) and
    tests that don't care about the HTTP side — just logs instead of
    calling out to a backend. Not used by the real ARQ task."""

    def __init__(self, *, job_id="local"):
        self._job_id = job_id

    async def check_status(self) -> dict:
        return {}

    async def progress(self, percent: int) -> None:
        logger.info("job %s: progress %d%%", self._job_id, percent)

    async def complete(self, result) -> None:
        logger.info(
            "job %s: completed (%d nodes, %d edges)",
            self._job_id,
            len(result.nodes),
            len(result.edges),
        )

    async def fail(self, result) -> None:
        logger.info("job %s: failed — %s", self._job_id, result.error.message if result.error else "unknown")

    async def complete_chat(
        self,
        *,
        map_id,
        assistant_message,
        summary=None,
        action_proposal=None,
        cited_node_ids=None,
        source_refs=None,
    ) -> None:
        logger.info("job %s: chat completed for map %s: %s", self._job_id, map_id, assistant_message[:50])

    async def fail_chat(self, *, map_id, error) -> None:
        logger.info("job %s: chat failed for map %s: %s", self._job_id, map_id, error)

    async def aclose(self) -> None:
        pass
