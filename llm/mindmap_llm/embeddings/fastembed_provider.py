"""
Local, 100% free embedding provider using FastEmbed (ONNX Runtime).
Zero API keys, zero external network latency, zero costs.

Default model: 'BAAI/bge-small-en-v1.5' (384-dimensional).
Also supports 'sentence-transformers/all-MiniLM-L6-v2' (384-dimensional).
"""

from __future__ import annotations

import asyncio

from embeddings.base import EmbeddingProvider


class FastEmbedProvider(EmbeddingProvider):
    def __init__(
        self,
        model_name: str = "BAAI/bge-small-en-v1.5",
        expected_dimension: int = 384,
        batch_size: int = 32,
    ):
        from fastembed import TextEmbedding

        self._model_name = model_name
        self._dimension = expected_dimension
        self._batch_size = batch_size
        self._model = TextEmbedding(model_name=model_name)

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def model_name(self) -> str:
        return f"fastembed:{self._model_name}"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        loop = asyncio.get_running_loop()

        def _compute() -> list[list[float]]:
            generator = self._model.embed(texts, batch_size=self._batch_size)
            return [arr.tolist() for arr in generator]

        vectors = await loop.run_in_executor(None, _compute)
        return self._validate(vectors)
