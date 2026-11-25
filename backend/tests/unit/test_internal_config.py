"""Unit tests for backend GET /internal/config endpoint (S3-BE8).

Verifies:
1. GET /internal/config returns HTTP 200 with active pipeline configuration.
2. Structure contains version, llm, prompts, and refresh_interval_s.
3. Security Invariant: No API keys, passwords, or secrets are exposed in response.
4. Endpoint is accessible at both /internal/config and /api/internal/config.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


@asynccontextmanager
async def _noop_lifespan(app):
    yield


@pytest.fixture(autouse=True)
def _patch_infrastructure():
    with (
        patch("core.supabase.is_supabase_configured", return_value=True),
        patch("core.supabase.SUPABASE_URL", "https://fake.supabase.co"),
        patch("core.supabase.ANON_KEY", "fake-anon-key"),
        patch("core.supabase.SERVICE_KEY", "fake-service-key"),
        patch("main.create_redis_pool", AsyncMock(return_value=None)),
        patch("main.create_db_pool", AsyncMock(return_value=None)),
    ):
        yield


@pytest.fixture()
def app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    from main import app as real_app

    real_app.router.lifespan_context = _noop_lifespan
    monkeypatch.setattr(
        real_app.state,
        "pool",
        SimpleNamespace(fetchval=AsyncMock(return_value={"enable_two_pass": True, "prompt_template_version": "v2"})),
        raising=False,
    )
    monkeypatch.setenv("PIPELINE_CONFIG_SERVICE_KEY", "test-config-key")
    return real_app


@pytest_asyncio.fixture()
async def client(app: FastAPI):
    transport = ASGITransport(app=app, client=("127.0.0.1", 50000))
    async with AsyncClient(
        transport=transport, base_url="http://test", headers={"X-Internal-Service-Key": "test-config-key"}
    ) as client:
        yield client


@pytest.mark.asyncio
async def test_get_internal_config_success(client: AsyncClient):
    resp = await client.get("/internal/config")
    assert resp.status_code == 200
    data = resp.json()

    assert "version" in data
    assert "llm" in data
    assert "prompts" in data
    assert "refresh_interval_s" in data

    llm = data["llm"]
    assert "provider" in llm
    assert "model" in llm
    assert "hyperparameters" in llm
    assert "temperature" in llm["hyperparameters"]
    assert "max_output_tokens" in llm["hyperparameters"]
    assert "request_timeout_s" in llm["hyperparameters"]

    prompts = data["prompts"]
    assert prompts["version"] == "v2"
    assert "templates" in prompts
    assert data["flags"]["enable_two_pass"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/internal/config", "/api/internal/config"])
async def test_internal_config_rejects_external_clients(app: FastAPI, path: str):
    transport = ASGITransport(app=app, client=("203.0.113.10", 50000))
    async with AsyncClient(transport=transport, base_url="http://test") as external:
        assert (await external.get(path)).status_code == 403
        assert (await external.get(path, headers={"X-Forwarded-For": "127.0.0.1"})).status_code == 403


@pytest.mark.asyncio
async def test_get_internal_config_no_secrets_exposed(client: AsyncClient):
    resp = await client.get("/internal/config")
    assert resp.status_code == 200
    data = resp.json()

    forbidden_substrings = ["api_key", "secret", "token", "password", "service_role"]
    for key in data:
        assert key.lower() not in forbidden_substrings
    for key in data.get("llm", {}):
        assert key.lower() not in forbidden_substrings

    raw_text = resp.text.lower()
    for forbidden in ["api_key", "secret_key", "password"]:
        assert f'"{forbidden}"' not in raw_text


@pytest.mark.asyncio
async def test_get_internal_config_api_prefix(client: AsyncClient):
    resp1 = await client.get("/internal/config")
    resp2 = await client.get("/api/internal/config")
    assert resp1.status_code == 200
    assert resp2.status_code == 200
    assert resp1.json() == resp2.json()
