"""
Embedding provider backed by Cloudflare Workers AI.

Uses Cloudflare Workers AI REST API to generate embeddings remotely.
- Free tier: 10,000 requests/day
- 0 MB server-side RAM (eliminates FastEmbed OOM risk on free-tier containers)
- Default model '@cf/baai/bge-small-en-v1.5' produces native 384-dimensional
  vectors, matching the Pinecone index and FastEmbed dimension with zero migration.
"""

from __future__ import annotations

import httpx
from embeddings.base import EmbeddingProvider


class CloudflareEmbeddingProvider(EmbeddingProvider):
    def __init__(
        self,
        account_id: str,
        api_token: str,
        model: str = "@cf/baai/bge-small-en-v1.5",
        expected_dimension: int = 384,
        batch_size: int = 32,
        timeout_s: float = 30.0,
    ):
        if not account_id:
            raise RuntimeError("CLOUDFLARE_ACCOUNT_ID is not set")
        if not api_token:
            raise RuntimeError("EMBEDDING_API_KEY (Cloudflare API token) is not set")

        self._account_id = account_id.strip()
        self._api_token = api_token.strip()
        self._model = model.strip()
        self._dimension = expected_dimension
        self._batch_size = max(1, batch_size)
        self._timeout_s = timeout_s

        self._client = httpx.AsyncClient(
            base_url="https://api.cloudflare.com/client/v4",
            headers={
                "Authorization": f"Bearer {self._api_token}",
                "Content-Type": "application/json",
            },
            timeout=timeout_s,
        )

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def model_name(self) -> str:
        return f"cloudflare:{self._model}"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        all_vectors: list[list[float]] = []
        model_path = self._model.lstrip("/")

        for start in range(0, len(texts), self._batch_size):
            batch = texts[start : start + self._batch_size]
            url = f"/accounts/{self._account_id}/ai/run/{model_path}"

            resp = await self._client.post(url, json={"text": batch})
            resp.raise_for_status()
            body = resp.json()

            if not body.get("success", True):
                errors = body.get("errors", [])
                raise RuntimeError(f"Cloudflare Workers AI embedding error: {errors}")

            result = body.get("result", {})
            data = result.get("data", [])

            # Handle 1D vector return when single text is provided
            if data and isinstance(data[0], (int, float)):
                all_vectors.append([float(x) for x in data])
            else:
                for item in data:
                    all_vectors.append([float(x) for x in item])

        return self._validate(all_vectors)

    async def aclose(self) -> None:
        await self._client.aclose()
