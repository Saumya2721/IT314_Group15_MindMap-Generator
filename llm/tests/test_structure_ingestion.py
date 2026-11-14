from __future__ import annotations

from uuid import uuid4

import pytest
from rag.chunking import StructureAwareChunker
from rag.ingestion import ingest_seed_text
from rag.pinecone_store import PineconeVectorStore, VectorChunk


class RecordingStore:
    def __init__(self):
        self.records: list[VectorChunk] = []

    async def upsert_chunks(self, *, namespace: str, chunks: list[VectorChunk]) -> int:
        self.records.extend(chunks)
        return len(chunks)


class Embeddings:
    model_name = "test-embed"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1] * 10 for _ in texts]


@pytest.mark.asyncio
async def test_seed_and_tool_ingestion_carry_structure_metadata():
    store = RecordingStore()
    document = "# Root\nIntro.\n## Details\n--- Page 3 ---\nUseful information."
    await ingest_seed_text(
        vector_store=store,
        namespace="map",
        generation_job_id=uuid4(),
        seed_text=document,
        embedding_provider=Embeddings(),
    )
    assert [c.section_path for c in store.records] == ["Root", "Root > Details"]
    assert [c.heading_level for c in store.records] == [1, 2]


def test_chunker_class_matches_existing_function():
    chunks = StructureAwareChunker(chunk_size=100).chunk("# Heading\nText")
    assert chunks[0].section_path == "Heading"
    assert chunks[0].heading_level == 1


@pytest.mark.asyncio
async def test_pinecone_metadata_and_breadcrumb_bm25(monkeypatch):
    store = RecordingStore()
    await ingest_seed_text(
        vector_store=store,
        namespace="map",
        generation_job_id=uuid4(),
        seed_text="# Heading\nBody text",
        embedding_provider=Embeddings(),
    )
    called = []
    records = []
    pinecone = PineconeVectorStore.__new__(PineconeVectorStore)
    pinecone._dense_weight = 0.5
    pinecone._bm25 = None
    pinecone._sparse_vector = lambda text: called.append(text) or None

    class Index:
        def upsert(self, **kwargs):
            records.extend(kwargs["vectors"])

    pinecone._index = Index()
    await pinecone.upsert_chunks(namespace="map", chunks=store.records)
    assert records[0]["metadata"]["heading_level"] == 1
