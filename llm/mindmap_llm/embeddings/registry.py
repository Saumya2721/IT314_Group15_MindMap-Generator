from __future__ import annotations

from config import Settings
from embeddings.base import EmbeddingProvider


def get_embedding_provider(settings: Settings) -> EmbeddingProvider:
    provider = settings.embedding.provider.lower()
    if provider == "huggingface":
        from embeddings.huggingface_provider import HuggingFaceEmbeddingProvider

        return HuggingFaceEmbeddingProvider(
            api_key=settings.embedding.api_key,
            api_base=settings.embedding.api_base,
            model=settings.embedding.model,
            expected_dimension=settings.embedding.dimension,
            batch_size=settings.embedding.batch_size,
        )
    if provider in {"fastembed", "local"}:
        from embeddings.fastembed_provider import FastEmbedProvider

        return FastEmbedProvider(
            model_name=settings.embedding.model,
            expected_dimension=settings.embedding.dimension,
            batch_size=settings.embedding.batch_size,
        )

    # Add new vendors here, e.g. "openai", "cohere", etc.
    raise ValueError(
        f"Unknown EMBEDDING_PROVIDER={provider!r}. "
        "Supported providers: 'fastembed', 'local', 'huggingface'."
    )
