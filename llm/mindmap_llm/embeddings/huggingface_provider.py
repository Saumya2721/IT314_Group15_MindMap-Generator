"""
Embedding provider backed by the Hugging Face Inference API.

The projection-to-1536 workaround from the Postgres version is GONE.
With Pinecone, `dimension` just needs to match whatever the Pinecone
index was created with (which should in turn match this model's native
output size) — see scripts/create_pinecone_index.py. If they don't
match, EmbeddingDimensionMismatch below fails loudly and immediately,
rather than silently reshaping vectors with a random matrix. That old
behavior was also the source of a real bug (K4): the projection matrix
was seeded with Python's `hash()`, which differs per process, so
vectors written by one run were incompatible with queries from another.
Removing the projection removes that whole bug class.
"""

from __future__ import annotations

import httpx
import numpy as np
from embeddings.base import EmbeddingProvider


class HuggingFaceEmbeddingProvider(EmbeddingProvider):
    def __init__(
        self,
        api_key: str,
        api_base: str,
        model: str,
        expected_dimension: int,
        batch_size: int = 32,
        timeout_s: float = 30.0,
    ):
        if not api_key:
            raise RuntimeError("EMBEDDING_API_KEY (Hugging Face token) is not set")
        self._model = model
        self._dimension = expected_dimension
        self._batch_size = batch_size
        self._client = httpx.AsyncClient(
            base_url=api_base.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout_s,
        )

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def model_name(self) -> str:
        return f"huggingface:{self._model}"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        all_vectors: list[np.ndarray] = []
        for start in range(0, len(texts), self._batch_size):
            batch = texts[start : start + self._batch_size]
            url = (
                f"/{self._model}/pipeline/feature-extraction"
                if "router.huggingface.co" in str(self._client.base_url)
                else f"/{self._model}"
            )
            resp = await self._client.post(
                url,
                json={"inputs": batch, "options": {"wait_for_model": True}},
            )
            resp.raise_for_status()
            raw = resp.json()
            # HF feature-extraction pipelines commonly return either
            # [batch, seq_len, hidden] (needs mean pooling) or
            # [batch, hidden] (already pooled). Handle both.
            arr = np.array(raw, dtype=np.float32)
            if arr.ndim == 3:
                arr = arr.mean(axis=1)
            all_vectors.append(arr)

        stacked = np.concatenate(all_vectors, axis=0) if len(all_vectors) > 1 else all_vectors[0]
        return self._validate(stacked.tolist())
