from __future__ import annotations

from dataclasses import dataclass

from callback.client import JobCallbackClient, NullCallbackClient
from config import Settings
from embeddings.base import EmbeddingProvider
from providers.base import LLMProvider
from providers.decision_model import DecisionModel
from rag.pinecone_store import PineconeVectorStore
from tools.base import Tool


@dataclass
class GenerationDeps:
    """Everything a generation run needs, gathered in one place. CHANGED
    in the worker restructure: `conn` (asyncpg) and `job_tracker`
    (Postgres-backed) are gone, replaced by `vector_store` (Pinecone)
    and `callback` (HTTP calls to the backend). This is still the ONLY
    thing tests have to swap out to run the pipeline fully mocked."""

    llm: LLMProvider
    decision_model: DecisionModel
    embedding_provider: EmbeddingProvider
    vector_store: PineconeVectorStore
    tools: dict[str, Tool]
    settings: Settings
    callback: JobCallbackClient | NullCallbackClient
