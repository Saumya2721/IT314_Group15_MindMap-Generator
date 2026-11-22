"""
Tests for the Cloudflare Workers AI embedding provider.
"""

from __future__ import annotations

import httpx
import pytest
from embeddings.base import EmbeddingDimensionMismatch
from embeddings.cloudflare_provider import CloudflareEmbeddingProvider
from embeddings.registry import get_embedding_provider


@pytest.mark.asyncio
async def test_cloudflare_init_validation():
    with pytest.raises(RuntimeError, match="CLOUDFLARE_ACCOUNT_ID is not set"):
        CloudflareEmbeddingProvider(account_id="", api_token="cfut_test")

    with pytest.raises(RuntimeError, match="EMBEDDING_API_KEY.*is not set"):
        CloudflareEmbeddingProvider(account_id="acc123", api_token="")


@pytest.mark.asyncio
async def test_cloudflare_properties():
    provider = CloudflareEmbeddingProvider(
        account_id="test_acc",
        api_token="test_token",
        model="@cf/baai/bge-small-en-v1.5",
        expected_dimension=384,
    )
    assert provider.dimension == 384
    assert provider.model_name == "cloudflare:@cf/baai/bge-small-en-v1.5"
    await provider.aclose()


@pytest.mark.asyncio
async def test_cloudflare_empty_texts():
    provider = CloudflareEmbeddingProvider(
        account_id="test_acc",
        api_token="test_token",
    )
    vectors = await provider.embed([])
    assert vectors == []
    await provider.aclose()


@pytest.mark.asyncio
async def test_cloudflare_embed_mocked(monkeypatch):
    provider = CloudflareEmbeddingProvider(
        account_id="test_acc",
        api_token="test_token",
        model="@cf/baai/bge-small-en-v1.5",
        expected_dimension=384,
        batch_size=2,
    )

    call_records = []

    async def mock_post(url, json=None, **kwargs):
        call_records.append({"url": url, "json": json})
        texts = json.get("text", [])
        mock_data = [[0.01 * (i + 1)] * 384 for i in range(len(texts))]
        response = httpx.Response(
            status_code=200,
            json={
                "result": {
                    "shape": [len(texts), 384],
                    "data": mock_data,
                },
                "success": True,
                "errors": [],
                "messages": [],
            },
            request=httpx.Request("POST", "https://api.cloudflare.com" + url),
        )
        return response

    monkeypatch.setattr(provider._client, "post", mock_post)

    texts = ["First chunk", "Second chunk", "Third chunk"]
    vectors = await provider.embed(texts)

    assert len(vectors) == 3
    assert all(len(v) == 384 for v in vectors)
    # Batch size was 2, so 3 items should result in 2 post calls (batch 1: 2 items, batch 2: 1 item)
    assert len(call_records) == 2
    assert len(call_records[0]["json"]["text"]) == 2
    assert len(call_records[1]["json"]["text"]) == 1

    await provider.aclose()


@pytest.mark.asyncio
async def test_cloudflare_dimension_mismatch(monkeypatch):
    provider = CloudflareEmbeddingProvider(
        account_id="test_acc",
        api_token="test_token",
        expected_dimension=384,
    )

    async def mock_post(url, json=None, **kwargs):
        # Return 256 dimensions instead of expected 384
        return httpx.Response(
            status_code=200,
            json={
                "result": {
                    "shape": [1, 256],
                    "data": [[0.1] * 256],
                },
                "success": True,
            },
            request=httpx.Request("POST", "https://api.cloudflare.com" + url),
        )

    monkeypatch.setattr(provider._client, "post", mock_post)

    with pytest.raises(EmbeddingDimensionMismatch, match="expected 384"):
        await provider.embed(["test text"])

    await provider.aclose()


@pytest.mark.asyncio
async def test_cloudflare_api_error_response(monkeypatch):
    provider = CloudflareEmbeddingProvider(
        account_id="test_acc",
        api_token="test_token",
    )

    async def mock_post(url, json=None, **kwargs):
        return httpx.Response(
            status_code=200,
            json={
                "result": {},
                "success": False,
                "errors": [{"code": 1000, "message": "Invalid token"}],
            },
            request=httpx.Request("POST", "https://api.cloudflare.com" + url),
        )

    monkeypatch.setattr(provider._client, "post", mock_post)

    with pytest.raises(RuntimeError, match="Cloudflare Workers AI embedding error"):
        await provider.embed(["test text"])

    await provider.aclose()


def test_registry_cloudflare_provider(monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "cloudflare")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acc_123")
    monkeypatch.setenv("EMBEDDING_API_KEY", "token_xyz")
    monkeypatch.setenv("EMBEDDING_MODEL", "@cf/baai/bge-small-en-v1.5")
    monkeypatch.setenv("EMBEDDING_DIMENSION", "384")

    # Clear lru_cache on get_settings
    from config import get_settings

    get_settings.cache_clear()
    settings = get_settings()

    provider = get_embedding_provider(settings)
    assert isinstance(provider, CloudflareEmbeddingProvider)
    assert provider.dimension == 384
    assert provider.model_name == "cloudflare:@cf/baai/bge-small-en-v1.5"
