from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from contracts.models import ChunkSource
from orchestration.nodes import reconstruct_seed_from_namespace
from rag.ingestion import StructureAwareChunker, ingest_seed_text, ingest_tool_result
from rag.pinecone_store import PineconeVectorStore, RetrievedChunkRecord


class Embeddings:
    model_name = "test"
    dimension = 2

    async def embed(self, texts):
        return [[0.2, 0.8] for _ in texts]


class RecordingStore:
    def __init__(self):
        self.records = []

    async def upsert_chunks(self, *, namespace, chunks):
        self.records.extend(chunks)
        return len(chunks)


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
    assert [c.page_number for c in store.records] == [None, 3]
    assert [c.char_length for c in store.records] == [len(c.chunk_text) for c in store.records]
    assert [c.chunk_index for c in store.records] == [0, 1]
    store.records.clear()
    await ingest_tool_result(
        vector_store=store,
        namespace="map",
        generation_job_id=uuid4(),
        text=document,
        source=ChunkSource.WEB_SEARCH,
        source_title="Article",
        source_url="https://example.org",
        embedding_provider=Embeddings(),
    )
    assert [c.chunk_index for c in store.records] == [0, 1]
    assert store.records[1].page_number == 3


def test_chunker_class_matches_existing_function():
    chunks = StructureAwareChunker(chunk_size=100).chunk("# Heading\nText")
    assert chunks[0].section_path == "Heading"
    assert chunks[0].heading_level == 1


@pytest.mark.asyncio
async def test_reconstruction_orders_and_removes_overlap():
    chunks = [
        RetrievedChunkRecord(
            id="b",
            chunk_text="beta gamma delta",
            source=ChunkSource.UPLOADED_SEED,
            source_title=None,
            source_url=None,
            score=1,
            chunk_index=1,
            section_path="One",
        ),
        RetrievedChunkRecord(
            id="a",
            chunk_text="alpha beta gamma",
            source=ChunkSource.UPLOADED_SEED,
            source_title=None,
            source_url=None,
            score=1,
            chunk_index=0,
            section_path="One",
        ),
        RetrievedChunkRecord(
            id="c",
            chunk_text="new section",
            source=ChunkSource.UPLOADED_SEED,
            source_title=None,
            source_url=None,
            score=1,
            chunk_index=2,
            section_path="Two",
        ),
    ]

    class Store:
        async def fetch_all_for_source(self, **kwargs):
            return chunks

    deps = SimpleNamespace(vector_store=Store(), embedding_provider=SimpleNamespace(dimension=2))
    result = await reconstruct_seed_from_namespace(deps, namespace="map")
    assert result == "alpha beta gamma delta\n\nnew section"


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
    assert called == ["Heading\nBody text"]
    assert records[0]["metadata"]["heading_level"] == 1
    assert records[0]["metadata"]["char_length"] == len("Body text")
    assert records[0]["metadata"]["chunk_index"] == 0
