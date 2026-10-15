from __future__ import annotations

from dataclasses import dataclass

from embeddings.base import EmbeddingProvider
from providers.decision_model import DecisionModel
from rag.pinecone_store import PineconeVectorStore, RetrievedChunkRecord


@dataclass
class RetrievedChunk:
    record: RetrievedChunkRecord
    relevance_score: (
        float  # 0-1, from rerank (falls back to Pinecone's own score if rerank is skipped)
    )


async def retrieve_context(
    *,
    vector_store: PineconeVectorStore,
    namespace: str,
    query: str,
    embedding_provider: EmbeddingProvider,
    top_k: int,
) -> list[RetrievedChunkRecord]:
    """Stage 1: hybrid dense+sparse retrieval via Pinecone, scoped to the
    map's namespace (RAG_Strategy.md: 'Classical RAG with hybrid
    retrieval (dense + BM25) for Sprint 1')."""
    [query_vector] = await embedding_provider.embed([query])
    return await vector_store.query(
        namespace=namespace, query_text=query, query_embedding=query_vector, top_k=top_k
    )


async def rerank_context(
    *,
    decision_model: DecisionModel,
    query: str,
    candidates: list[RetrievedChunkRecord],
    keep_top_k: int,
) -> list[RetrievedChunk]:
    """Stage 2: unchanged from the Postgres version — scores candidates
    for relevance and keeps the top `keep_top_k`. Still a Jev-shaped
    (score primitive) call, still falls back automatically via
    providers.registry.SafeDecisionModel."""
    if not candidates:
        return []
    result = await decision_model.score(
        state=f"User's mind-map topic/query: {query}",
        items=[c.chunk_text for c in candidates],
        question="How relevant is this passage to building a mind map about the stated topic?",
    )
    scored = list(zip(candidates, result.scores, strict=False))
    scored.sort(key=lambda pair: pair[1], reverse=True)
    top = scored[:keep_top_k]
    return [RetrievedChunk(record=rec, relevance_score=score) for rec, score in top]
