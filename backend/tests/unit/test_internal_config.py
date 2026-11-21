"""Unit tests for backend GET /internal/config endpoint (S3-BE8).

Verifies:
1. GET /internal/config returns HTTP 200 with active pipeline configuration.
2. Structure contains version, llm, prompts, and refresh_interval_s.
3. Security Invariant: No API keys, passwords, or secrets are exposed in response.
4. Endpoint is accessible at both /internal/config and /api/internal/config.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


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
def app() -> FastAPI:
    from main import app as real_app

    real_app.router.lifespan_context = _noop_lifespan
    return real_app


@pytest.fixture()
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


def test_get_internal_config_success(client: TestClient):
    resp = client.get("/internal/config")
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
    assert "version" in prompts
    assert "templates" in prompts


def test_get_internal_config_no_secrets_exposed(client: TestClient):
    resp = client.get("/internal/config")
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


def test_get_internal_config_api_prefix(client: TestClient):
    resp1 = client.get("/internal/config")
    resp2 = client.get("/api/internal/config")
    assert resp1.status_code == 200
    assert resp2.status_code == 200
    assert resp1.json() == resp2.json()
