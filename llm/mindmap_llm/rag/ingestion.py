from __future__ import annotations

from uuid import UUID

from contracts.models import ChunkSource
from embeddings.base import EmbeddingProvider
from rag.chunking import (
    DEFAULT_CHUNK_OVERLAP_CHARS,
    DEFAULT_CHUNK_SIZE_CHARS,
    StructureAwareChunker,
    TextChunk,
    chunk_text,
)
from rag.pinecone_store import PineconeVectorStore, VectorChunk

__all__ = [
    "DEFAULT_CHUNK_OVERLAP_CHARS",
    "DEFAULT_CHUNK_SIZE_CHARS",
    "StructureAwareChunker",
    "TextChunk",
    "chunk_text",
    "ingest_seed_text",
    "ingest_tool_result",
]


async def ingest_seed_text(
    *,
    vector_store: PineconeVectorStore,
    namespace: str,
    generation_job_id: UUID,
    seed_text: str,
    embedding_provider: EmbeddingProvider,
) -> int:
    """Chunks + embeds the normalized seed text and upserts it into
    Pinecone under the map's namespace, source=uploaded_seed."""
    chunks = chunk_text(seed_text)
    if not chunks:
        return 0
    vectors = await embedding_provider.embed([str(c) for c in chunks])
    records = [
        VectorChunk(
            chunk_text=chunk,
            embedding=vector,
            embedding_model=embedding_provider.model_name,
            source=ChunkSource.UPLOADED_SEED,
            generation_job_id=generation_job_id,
            section_path=getattr(chunk, "section_path", None),
            heading_level=getattr(chunk, "heading_level", None),
        )
        for chunk, vector in zip(chunks, vectors)
    ]
    return await vector_store.upsert_chunks(namespace=namespace, chunks=records)


async def ingest_tool_result(
    *,
    vector_store: PineconeVectorStore,
    namespace: str,
    generation_job_id: UUID,
    text: str,
    source: ChunkSource,
    source_title: str | None,
    source_url: str,
    embedding_provider: EmbeddingProvider,
) -> int:
    """Same as ingest_seed_text but for web_search/arxiv tool output,
    where source_url is always populated (mirrors the old
    chk_embeddings_source_url constraint, now enforced by convention
    rather than a DB CHECK)."""
    chunks = chunk_text(text)
    if not chunks:
        return 0
    vectors = await embedding_provider.embed([str(c) for c in chunks])
    records = [
        VectorChunk(
            chunk_text=chunk,
            embedding=vector,
            embedding_model=embedding_provider.model_name,
            source=source,
            generation_job_id=generation_job_id,
            source_title=source_title,
            source_url=source_url,
            section_path=getattr(chunk, "section_path", None),
            heading_level=getattr(chunk, "heading_level", None),
        )
        for chunk, vector in zip(chunks, vectors)
    ]
    return await vector_store.upsert_chunks(namespace=namespace, chunks=records)
