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

import logging
from uuid import UUID

import httpx
from contracts.models import GenerationResult

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

    def __init__(
        self, *, job_id: UUID | str, base_url: str, token: str, timeout_s: float = 10.0
    ) -> None:
        self._job_id = job_id
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout_s,
        )
        self._terminal_sent = False

    async def progress(self, percent: int) -> None:
        if self._terminal_sent:
            raise CallbackAlreadySent(
                f"job {self._job_id}: progress() called after a terminal callback"
            )
        try:
            resp = await self._client.post(
                f"/internal/jobs/{self._job_id}/progress", json={"progress_percent": percent}
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            # A missed progress tick is not fatal to the generation itself —
            # log it and keep going rather than failing the whole job over
            # a UI nicety. complete()/fail() below are NOT allowed to do this.
            logger.warning(
                "job %s: progress callback failed (percent=%d): %s", self._job_id, percent, exc
            )

    async def complete(self, result: GenerationResult) -> None:
        if self._terminal_sent:
            raise CallbackAlreadySent(f"job {self._job_id}: complete() called twice")
        self._terminal_sent = True
        resp = await self._client.post(
            f"/internal/jobs/{self._job_id}/complete", content=result.model_dump_json()
        )
        resp.raise_for_status()

    async def fail(self, result: GenerationResult) -> None:
        if self._terminal_sent:
            raise CallbackAlreadySent(f"job {self._job_id}: fail() called twice")
        self._terminal_sent = True
        resp = await self._client.post(
            f"/internal/jobs/{self._job_id}/fail", content=result.model_dump_json()
        )
        resp.raise_for_status()

    async def aclose(self) -> None:
        await self._client.aclose()


class NullCallbackClient:
    """No-op stand-in for local/manual runs (scripts/run_local.py) and
    tests that don't care about the HTTP side — just logs instead of
    calling out to a backend. Not used by the real ARQ task."""

    def __init__(self, *, job_id: UUID | str = "local") -> None:
        self._job_id = job_id

    async def progress(self, percent: int) -> None:
        logger.info("job %s: progress %d%%", self._job_id, percent)

    async def complete(self, result: GenerationResult) -> None:
        logger.info(
            "job %s: completed (%d nodes, %d edges)",
            self._job_id,
            len(result.nodes),
            len(result.edges),
        )

    async def fail(self, result: GenerationResult) -> None:
        logger.info(
            "job %s: failed — %s", self._job_id, result.error.message if result.error else "unknown"
        )

    async def aclose(self) -> None:
        pass
