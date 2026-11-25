"""Tests for admin pipeline settings and audit logging."""

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
import pytest_asyncio
from auth.dependencies import require_admin_role
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from routes.admin import router

ADMIN_ID = UUID("ca71e216-9bcb-4880-a50c-4ab219228e7d")


@pytest_asyncio.fixture
async def client_and_db(monkeypatch):
    app = FastAPI()
    app.include_router(router)

    async def admin_user():
        return SimpleNamespace(id=ADMIN_ID)

    app.dependency_overrides[require_admin_role] = admin_user
    monkeypatch.setenv("ENABLE_TWO_PASS", "false")
    monkeypatch.setenv("RESEARCH_ENGINE", "subgraph")
    monkeypatch.setenv("IMAGE_EXTRACTOR", "gemini")
    conn = SimpleNamespace(
        fetchval=AsyncMock(return_value={"enable_two_pass": True, "research_engine": "inline"}),
        execute=AsyncMock(),
    )

    @asynccontextmanager
    async def acquire():
        yield conn

    @asynccontextmanager
    async def transaction():
        yield

    conn.transaction = transaction
    app.state.pool = SimpleNamespace(fetchval=AsyncMock(return_value={"research_engine": "inline"}), acquire=acquire)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, conn


@pytest.mark.asyncio
async def test_get_config_combines_overrides_with_defaults(client_and_db):
    client, _ = client_and_db
    response = await client.get("/admin/config")
    assert response.status_code == 200
    assert response.json()["enable_two_pass"] is False
    assert response.json()["research_engine"] == "inline"
    assert response.json()["image_extractor"] == "gemini"
    assert response.json()["prompt_template_version"] == "v1.0"
    assert "llm_provider" in response.json()
    assert "llm_model" in response.json()
    assert "temperature" in response.json()
    assert "langsmith_tracing" in response.json()


@pytest.mark.asyncio
async def test_patch_config_merges_and_audits(client_and_db):
    client, conn = client_and_db
    response = await client.patch("/admin/config", json={"enable_two_pass": True})
    assert response.status_code == 200
    assert response.json()["enable_two_pass"] is True
    assert response.json()["research_engine"] == "inline"
    assert json.loads(conn.fetchval.await_args.args[1]) == {"enable_two_pass": True}
    _, actor, action, target, metadata = conn.execute.await_args.args
    assert (actor, action, target) == (ADMIN_ID, "pipeline_config.update", None)
    assert json.loads(metadata) == {"changes": {"enable_two_pass": True}}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"enable_two_pass": "true"},
        {"enable_two_pass": None},
        {"research_engine": "unknown"},
        {"image_extractor": "unknown"},
        {"prompt_template_version": " "},
        {"api_key": "test"},
    ],
)
async def test_patch_rejects_invalid_settings_before_db_write(client_and_db, payload):
    client, conn = client_and_db
    assert (await client.patch("/admin/config", json=payload)).status_code == 422
    conn.fetchval.assert_not_awaited()
    conn.execute.assert_not_awaited()
