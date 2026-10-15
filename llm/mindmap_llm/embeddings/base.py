"""
Abstract embedding provider.

Previously this had to force every model to a fixed 1536-dim output to
match a Postgres VECTOR(1536) column (and a random-projection hack to
get there). With Pinecone, the index is created once, out of band (see
scripts/create_pinecone_index.py), at whatever `dimension` the
configured model actually produces — so `dimension` here is now purely
informational/self-reporting, used to size the index correctly and to
catch an accidental mid-deployment model swap, not to force a reshape.

RAG_Strategy.md's "embedding model must be consistent per generation
job" rule still applies — `model_name` is stored as chunk metadata on
every vector specifically so that's checkable later if needed.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class EmbeddingDimensionMismatch(Exception):
    """Raised if a provider's actual output doesn't match the `dimension`
    it advertises — almost always a sign the model name and the
    Pinecone index were configured inconsistently."""


class EmbeddingProvider(ABC):
    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Returns one vector per input text, same order. Implementations
        should batch internally per config.EmbeddingConfig.batch_size."""
        raise NotImplementedError

    @property
    @abstractmethod
    def dimension(self) -> int:
        """The dimension this provider's vectors actually have. Must
        match the Pinecone index's configured dimension."""
        raise NotImplementedError

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Stored in every chunk's metadata — lets you detect mixed
        embedding models within one namespace later."""
        raise NotImplementedError

    def _validate(self, vectors: list[list[float]]) -> list[list[float]]:
        for v in vectors:
            if len(v) != self.dimension:
                raise EmbeddingDimensionMismatch(
                    f"{self.model_name} produced a {len(v)}-dim vector, "
                    f"expected {self.dimension} — check EMBEDDING_DIMENSION "
                    f"matches both the model and the Pinecone index."
                )
        return vectors
