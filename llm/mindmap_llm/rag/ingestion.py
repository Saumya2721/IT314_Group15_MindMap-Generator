from __future__ import annotations

import re
from uuid import UUID

from contracts.models import ChunkSource
from embeddings.base import EmbeddingProvider
from rag.pinecone_store import PineconeVectorStore, VectorChunk

DEFAULT_CHUNK_SIZE_CHARS = 1200
DEFAULT_CHUNK_OVERLAP_CHARS = 150


def chunk_text(
    text: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE_CHARS,
    overlap: int = DEFAULT_CHUNK_OVERLAP_CHARS,
) -> list[str]:
    """Simple paragraph-aware sliding-window chunker. Unchanged from the
    Postgres version — chunking never depended on where chunks end up
    stored."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    current = ""
    for para in paragraphs:
        if len(current) + len(para) + 2 <= chunk_size:
            current = f"{current}\n\n{para}" if current else para
        else:
            if current:
                chunks.append(current)
            if len(para) > chunk_size:
                start = 0
                while start < len(para):
                    end = start + chunk_size
                    chunks.append(para[start:end])
                    start = end - overlap
                current = ""
            else:
                current = para
    if current:
        chunks.append(current)
    return chunks or ([text] if text.strip() else [])


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
    vectors = await embedding_provider.embed(chunks)
    records = [
        VectorChunk(
            chunk_text=chunk,
            embedding=vector,
            embedding_model=embedding_provider.model_name,
            source=ChunkSource.UPLOADED_SEED,
            generation_job_id=generation_job_id,
        )
        for chunk, vector in zip(chunks, vectors, strict=False)
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
    vectors = await embedding_provider.embed(chunks)
    records = [
        VectorChunk(
            chunk_text=chunk,
            embedding=vector,
            embedding_model=embedding_provider.model_name,
            source=source,
            generation_job_id=generation_job_id,
            source_title=source_title,
            source_url=source_url,
        )
        for chunk, vector in zip(chunks, vectors, strict=False)
    ]
    return await vector_store.upsert_chunks(namespace=namespace, chunks=records)
