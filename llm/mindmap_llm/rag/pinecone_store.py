"""
Vector storage/retrieval on Pinecone, replacing the Postgres+pgvector
path (db/repository.py::hybrid_retrieve / insert_embeddings).

Namespacing: one Pinecone namespace per mind map (`namespace=str(map_id)`).
This is the direct replacement for the old `WHERE map_id = $1` filter —
namespace isolation means one map's vectors are not even scanned by
another map's query, not just filtered out after the fact.

Hybrid retrieval: Pinecone has no server-side BM25 the way Postgres
full-text search gave us for free. To keep the RAG strategy's "hybrid
dense + BM25" decision, this uses Pinecone's documented hybrid pattern —
a single query sent with BOTH a dense and a sparse vector, scaled by
`alpha` (dense_weight below). The sparse vector comes from
`pinecone_text.BM25Encoder`. If `pinecone-text` isn't installed or the
encoder fails to initialize, this falls back to dense-only search and
logs it, rather than failing.

The embedding dimension is no longer fixed at 1536: the Pinecone index
is created (once, out of band — see scripts/create_pinecone_index.py)
at whatever dimension the configured EmbeddingProvider actually
produces. This removes the random-projection workaround that used to
live in embeddings/huggingface_provider.py entirely.

NOT VERIFIED AGAINST A LIVE INDEX: this module was tested only against
the in-memory fake in tests/conftest.py. Check against a real Pinecone
index before relying on it: sparse+dense upserts/queries, and the
fetch_all_for_source() approach below.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from uuid import UUID, uuid4

from contracts.models import ChunkSource

logger = logging.getLogger(__name__)


@dataclass
class VectorChunk:
    """One chunk, ready to upsert. Mirrors the old EmbeddingRecord, minus
    the Postgres-specific fields (generation_job_id stays as metadata,
    not a foreign key)."""
    chunk_text: str
    embedding: list[float]
    embedding_model: str = "default"
    source: ChunkSource = ChunkSource.UPLOADED_SEED
    generation_job_id: UUID | None = None
    source_title: str | None = None
    source_url: str | None = None
    id: str = field(default_factory=lambda: str(uuid4()))


@dataclass
class RetrievedChunkRecord:
    """What queries return — enough to feed rag/retrieval.py's rerank
    step without it needing to know this is Pinecone underneath."""
    id: str
    chunk_text: str
    source: ChunkSource
    source_title: str | None
    source_url: str | None
    score: float  # Pinecone's own similarity score (pre-rerank)


class PineconeVectorStore:
    def __init__(self, api_key: str, index_name: str, dense_weight: float = 0.5):
        try:
            from pinecone import Pinecone
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("pinecone package not installed. `pip install pinecone`.") from exc

        self._pc = Pinecone(api_key=api_key)
        self._index = self._pc.Index(index_name)
        self._dense_weight = dense_weight
        self._bm25 = self._build_bm25_encoder()

    def _build_bm25_encoder(self):
        try:
            from pinecone_text.sparse import BM25Encoder
            encoder = BM25Encoder.default()  # pre-fit general-English defaults; good enough to start
            return encoder
        except ImportError:
            logger.info("pinecone-text not installed — hybrid search will fall back to dense-only.")
            return None
        except Exception as exc:  # noqa: BLE001 - never let encoder setup break vector storage
            logger.warning("Could not initialize BM25 sparse encoder, falling back to dense-only: %s", exc)
            return None

    def _sparse_vector(self, text: str) -> dict | None:
        if self._bm25 is None:
            return None
        try:
            return self._bm25.encode_documents([text])[0]
        except Exception as exc:  # noqa: BLE001
            logger.warning("BM25 encoding failed for one chunk, storing dense-only: %s", exc)
            return None

    def _sparse_query_vector(self, text: str) -> dict | None:
        if self._bm25 is None:
            return None
        try:
            return self._bm25.encode_queries([text])[0]
        except Exception as exc:  # noqa: BLE001
            logger.warning("BM25 query encoding failed, querying dense-only: %s", exc)
            return None

    @staticmethod
    def _scale(dense: list[float], sparse: dict | None, alpha: float) -> tuple[list[float], dict | None]:
        """Pinecone's documented convex-combination hybrid scaling."""
        if sparse is None:
            return dense, None
        scaled_dense = [v * alpha for v in dense]
        scaled_sparse = {
            "indices": sparse["indices"],
            "values": [v * (1 - alpha) for v in sparse["values"]],
        }
        return scaled_dense, scaled_sparse

    async def upsert_chunks(self, *, namespace: str, chunks: list[VectorChunk]) -> int:
        if not chunks:
            return 0

        vectors = []
        for c in chunks:
            sparse = self._sparse_vector(c.chunk_text)
            dense, sparse = self._scale(c.embedding, sparse, self._dense_weight)
            entry = {
                "id": c.id,
                "values": dense,
                "metadata": {
                    "chunk_text": c.chunk_text,
                    "embedding_model": c.embedding_model,
                    "source": c.source.value,
                    "generation_job_id": str(c.generation_job_id),
                    "source_title": c.source_title or "",
                    "source_url": c.source_url or "",
                },
            }
            if sparse is not None:
                entry["sparse_values"] = sparse
            vectors.append(entry)

        await asyncio.to_thread(self._index.upsert, vectors=vectors, namespace=namespace)
        return len(vectors)

    async def query(
        self,
        *,
        namespace: str,
        query_text: str,
        query_embedding: list[float],
        top_k: int,
        source_filter: ChunkSource | None = None,
    ) -> list[RetrievedChunkRecord]:
        sparse = self._sparse_query_vector(query_text)
        dense, sparse = self._scale(query_embedding, sparse, self._dense_weight)

        kwargs: dict = {
            "namespace": namespace,
            "vector": dense,
            "top_k": top_k,
            "include_metadata": True,
        }
        if sparse is not None:
            kwargs["sparse_vector"] = sparse
        if source_filter is not None:
            kwargs["filter"] = {"source": {"$eq": source_filter.value}}

        result = await asyncio.to_thread(self._index.query, **kwargs)

        return [
            RetrievedChunkRecord(
                id=match["id"],
                chunk_text=match["metadata"].get("chunk_text", ""),
                source=ChunkSource(match["metadata"].get("source", "uploaded_seed")),
                source_title=match["metadata"].get("source_title") or None,
                source_url=match["metadata"].get("source_url") or None,
                score=match["score"],
            )
            for match in result.get("matches", [])
        ]

    async def fetch_all_for_source(
        self, *, namespace: str, source: ChunkSource, dimension: int, limit: int = 1000
    ) -> list[RetrievedChunkRecord]:
        """Used by regeneration to reconstruct the original seed text:
        pulls every `uploaded_seed` chunk in the map's namespace. Pinecone
        has no "list all by metadata", so this issues a query with a
        throwaway unit vector (non-zero, since some index configs reject
        all-zero query vectors) and a high top_k, filtered by source.
        Adequate at seed-chunk volumes (dozens), not a general scan.
        Returned order is by similarity to that throwaway vector, i.e.
        arbitrary — callers must not rely on chunk order."""
        probe_vector = [1.0] + [0.0] * (dimension - 1)
        result = await asyncio.to_thread(
            self._index.query,
            namespace=namespace,
            vector=probe_vector,
            top_k=limit,
            include_metadata=True,
            filter={"source": {"$eq": source.value}},
        )
        return [
            RetrievedChunkRecord(
                id=match["id"],
                chunk_text=match["metadata"].get("chunk_text", ""),
                source=ChunkSource(match["metadata"].get("source", "uploaded_seed")),
                source_title=match["metadata"].get("source_title") or None,
                source_url=match["metadata"].get("source_url") or None,
                score=match["score"],
            )
            for match in result.get("matches", [])
        ]
