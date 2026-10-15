from __future__ import annotations

import json
import uuid
import httpx
import pytest

from callback.client import CallbackAlreadySent, JobCallbackClient, NullCallbackClient
from contracts.models import GenerationError, GenerationResult


def _make_dummy_result(job_id: uuid.UUID, status: str = "completed") -> GenerationResult:
    map_id = uuid.uuid4()
    if status == "completed":
        return GenerationResult(
            job_id=job_id,
            map_id=map_id,
            status="completed",
            nodes=[],
            edges=[],
        )
    return GenerationResult(
        job_id=job_id,
        map_id=map_id,
        status="failed",
        error=GenerationError(stage="validate", error_type="Error", message="Boom", retryable=False),
    )


@pytest.mark.asyncio
async def test_job_callback_client_progress_success():
    job_id = uuid.uuid4()
    requests_received = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests_received.append(request)
        return httpx.Response(200, json={"ok": True})

    client = JobCallbackClient(job_id=job_id, base_url="https://api.test", token="secret-token")
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.test")

    await client.progress(50)
    await client.aclose()

    assert len(requests_received) == 1
    req = requests_received[0]
    assert req.method == "POST"
    assert req.url.path == f"/internal/jobs/{job_id}/progress"
    payload = json.loads(req.content.decode("utf-8"))
    assert payload == {"progress_percent": 50}


@pytest.mark.asyncio
async def test_job_callback_client_progress_error_swallowed(caplog):
    job_id = uuid.uuid4()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "server down"})

    client = JobCallbackClient(job_id=job_id, base_url="https://api.test", token="secret-token")
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.test")

    # Should not raise exception
    await client.progress(25)
    await client.aclose()


@pytest.mark.asyncio
async def test_job_callback_client_complete():
    job_id = uuid.uuid4()
    requests_received = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests_received.append(request)
        return httpx.Response(200, json={"status": "ok"})

    client = JobCallbackClient(job_id=job_id, base_url="https://api.test", token="secret-token")
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.test")

    result = _make_dummy_result(job_id, status="completed")
    await client.complete(result)
    await client.aclose()

    assert len(requests_received) == 1
    req = requests_received[0]
    assert req.method == "POST"
    assert req.url.path == f"/internal/jobs/{job_id}/complete"
    assert json.loads(req.content.decode("utf-8"))["status"] == "completed"


@pytest.mark.asyncio
async def test_job_callback_client_fail():
    job_id = uuid.uuid4()
    requests_received = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests_received.append(request)
        return httpx.Response(200, json={"status": "ok"})

    client = JobCallbackClient(job_id=job_id, base_url="https://api.test", token="secret-token")
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.test")

    result = _make_dummy_result(job_id, status="failed")
    await client.fail(result)
    await client.aclose()

    assert len(requests_received) == 1
    req = requests_received[0]
    assert req.method == "POST"
    assert req.url.path == f"/internal/jobs/{job_id}/fail"
    assert json.loads(req.content.decode("utf-8"))["status"] == "failed"


@pytest.mark.asyncio
async def test_job_callback_client_terminal_double_call_raises():
    job_id = uuid.uuid4()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "ok"})

    client = JobCallbackClient(job_id=job_id, base_url="https://api.test", token="secret-token")
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.test")

    result = _make_dummy_result(job_id, status="completed")
    await client.complete(result)

    with pytest.raises(CallbackAlreadySent):
        await client.complete(result)

    with pytest.raises(CallbackAlreadySent):
        await client.fail(result)

    with pytest.raises(CallbackAlreadySent):
        await client.progress(80)

    await client.aclose()


@pytest.mark.asyncio
async def test_job_callback_client_terminal_http_failure_raises():
    job_id = uuid.uuid4()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="Bad Gateway")

    client = JobCallbackClient(job_id=job_id, base_url="https://api.test", token="secret-token")
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.test")

    result = _make_dummy_result(job_id, status="completed")
    with pytest.raises(httpx.HTTPStatusError):
        await client.complete(result)

    await client.aclose()


@pytest.mark.asyncio
async def test_null_callback_client():
    client = NullCallbackClient(job_id="test-job")
    result = _make_dummy_result(uuid.uuid4(), status="completed")

    # Verify no-op methods execute cleanly
    await client.progress(10)
    await client.complete(result)
    await client.fail(result)
    await client.aclose()
