"""
Test fixtures and shared fakes for the LLM worker test suite.

Fakes cover all four dependencies that GenerationDeps bundles:
  - FakeLLMProvider       : scripted responses for the generation LLM
  - FakeDecisionModel     : always returns the first label / score 1.0
  - FakeVectorStore       : in-memory Pinecone stand-in
  - FakeCallbackClient    : records progress/complete/fail calls in-process

FakeConnection and fake_conn (asyncpg stand-ins for JobTracker) have been
removed — JobTracker is gone; the worker uses JobCallbackClient instead.
"""

from __future__ import annotations

import pytest
from callback.client import CallbackAlreadySent
from contracts.models import GenerationResult
from providers.base import LLMProvider, LLMResponse, Message, ToolSpec
from providers.decision_model import ClassifyResult, DecisionModel, ScoreResult
from rag.pinecone_store import RetrievedChunkRecord, VectorChunk

# ---------------------------------------------------------------------------
# LLM fake
# ---------------------------------------------------------------------------


class FakeLLMProvider(LLMProvider):
    """Returns a scripted sequence of responses, one per call, so a test
    can simulate 'bad output, then good output on retry' deterministically."""

    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self.calls: list[list[Message]] = []

    @property
    def name(self) -> str:
        return "fake-llm"

    async def generate(
        self,
        messages: list[Message],
        *,
        system: str | None = None,
        tools: list[ToolSpec] | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.2,
    ) -> LLMResponse:
        self.calls.append(messages)
        text = self._responses.pop(0) if self._responses else ""
        return LLMResponse(text=text, tool_calls=[], stop_reason="end_turn")


# ---------------------------------------------------------------------------
# Decision model fake
# ---------------------------------------------------------------------------


class FakeDecisionModel(DecisionModel):
    """Always picks the first label with full confidence; gives every item
    score 1.0. Enough to let the pipeline proceed without Jev."""

    @property
    def name(self) -> str:
        return "fake-decision-model"

    async def classify(self, *, state: str, question: str, labels: list[str]) -> ClassifyResult:
        return ClassifyResult(label=labels[0], confidence=1.0)

    async def score(self, *, state: str, items: list[str], question: str) -> ScoreResult:
        return ScoreResult(scores=[1.0] * len(items))

    async def health_check(self) -> bool:
        return True


# ---------------------------------------------------------------------------
# Vector store fake  (in-memory, no Pinecone SDK needed)
# ---------------------------------------------------------------------------


class FakeVectorStore:
    """Minimal in-memory replacement for PineconeVectorStore.

    Stores chunks in a plain list per namespace; retrieval just returns all
    stored chunks (no actual similarity — that's the real Pinecone's job).
    Enough to let the pipeline flow through ingest → retrieve without errors.
    """

    def __init__(self):
        self._store: dict[str, list[VectorChunk]] = {}

    async def upsert_chunks(self, *, namespace: str, chunks: list[VectorChunk]) -> int:
        self._store.setdefault(namespace, []).extend(chunks)
        return len(chunks)

    async def query(
        self,
        *,
        namespace: str,
        query_text: str,
        query_embedding: list[float],
        top_k: int,
        source_filter=None,
    ) -> list[RetrievedChunkRecord]:
        chunks = self._store.get(namespace, [])
        if source_filter is not None:
            chunks = [c for c in chunks if c.source == source_filter]
        return [
            RetrievedChunkRecord(
                id=c.id,
                chunk_text=c.chunk_text,
                source=c.source,
                source_title=c.source_title,
                source_url=c.source_url,
                score=1.0,
            )
            for c in chunks[:top_k]
        ]

    async def fetch_all_for_source(
        self, *, namespace: str, source, dimension: int, limit: int = 1000
    ) -> list[RetrievedChunkRecord]:
        return await self.query(
            namespace=namespace,
            query_text="",
            query_embedding=[],
            top_k=limit,
            source_filter=source,
        )


# ---------------------------------------------------------------------------
# Callback client fake
# ---------------------------------------------------------------------------


class FakeCallbackClient:
    """Records every call in-process instead of making HTTP requests.
    Enforces the same double-call guard as the real JobCallbackClient so
    tests can assert the terminal callback was sent exactly once.
    """

    def __init__(self):
        self.progress_calls: list[int] = []
        self.complete_result: GenerationResult | None = None
        self.fail_result: GenerationResult | None = None
        self._terminal_sent = False

    async def progress(self, percent: int) -> None:
        if self._terminal_sent:
            raise CallbackAlreadySent("progress() after terminal")
        self.progress_calls.append(percent)

    async def complete(self, result: GenerationResult) -> None:
        if self._terminal_sent:
            raise CallbackAlreadySent("complete() called twice")
        self._terminal_sent = True
        self.complete_result = result

    async def fail(self, result: GenerationResult) -> None:
        if self._terminal_sent:
            raise CallbackAlreadySent("fail() called twice")
        self._terminal_sent = True
        self.fail_result = result

    async def aclose(self) -> None:
        pass

    @property
    def terminal_sent(self) -> bool:
        return self._terminal_sent


# ---------------------------------------------------------------------------
# Embedding provider fake
# ---------------------------------------------------------------------------


class FakeEmbeddingProvider:
    """Produces fixed-dimension dummy vectors for tests."""

    def __init__(self, dimension: int = 1024):
        self._dimension = dimension

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def model_name(self) -> str:
        return "fake-embeddings"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1] * self._dimension for _ in texts]


# ---------------------------------------------------------------------------
# Pytest fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_llm():
    return FakeLLMProvider


@pytest.fixture
def fake_decision_model():
    return FakeDecisionModel()


@pytest.fixture
def fake_embedding_provider():
    return FakeEmbeddingProvider()


@pytest.fixture
def fake_vector_store():
    return FakeVectorStore()


@pytest.fixture
def fake_callback():
    return FakeCallbackClient()


# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

VALID_DRAFT_PAYLOAD = {
    "nodes": [
        {
            "id": "n1",
            "label": "Photosynthesis",
            "description": "Root concept",
            "node_type": "default",
        },
        {"id": "n2", "label": "Light Reactions", "description": None, "node_type": "default"},
        {"id": "n3", "label": "Calvin Cycle", "description": None, "node_type": "default"},
    ],
    "edges": [
        {"source": "n1", "target": "n2", "label": "includes"},
        {"source": "n1", "target": "n3", "label": "includes"},
    ],
}
