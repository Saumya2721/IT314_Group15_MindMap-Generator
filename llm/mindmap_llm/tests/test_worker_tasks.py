from __future__ import annotations

import json
import uuid

import pytest
from config import Settings
from contracts.models import ChunkSource, GenerationRequest, JobType
from rag.pinecone_store import VectorChunk
from tests.conftest import (
    VALID_DRAFT_PAYLOAD,
    FakeCallbackClient,
    FakeDecisionModel,
    FakeEmbeddingProvider,
    FakeLLMProvider,
    FakeVectorStore,
)
from worker.tasks import generate_mindmap


class ExplodingLLM(FakeLLMProvider):
    async def generate(self, *args, **kwargs):
        raise RuntimeError("Simulated unhandled LLM crash")


def _build_request_payload(
    job_type: JobType = JobType.GENERATE,
    topic: str | None = "Photosynthesis",
    ideas: str | None = "Light reactions, Calvin cycle",
    regeneration_instruction: str | None = None,
) -> dict:
    req = GenerationRequest(
        job_id=uuid.uuid4(),
        map_id=uuid.uuid4(),
        job_type=job_type,
        topic=topic,
        ideas=ideas,
        regeneration_instruction=regeneration_instruction,
        callback_base_url="https://api.test",
        callback_token="test-token",
    )
    return req.model_dump(mode="json")


@pytest.mark.asyncio
async def test_generate_mindmap_success():
    payload = _build_request_payload()
    callback = FakeCallbackClient()
    ctx = {
        "llm": FakeLLMProvider([json.dumps(VALID_DRAFT_PAYLOAD)]),
        "decision_model": FakeDecisionModel(),
        "embedding_provider": FakeEmbeddingProvider(),
        "vector_store": FakeVectorStore(),
        "tools": {},
        "settings": Settings(),
        "callback": callback,
    }

    result = await generate_mindmap(ctx, payload)

    assert result["status"] == "completed"
    assert len(result["nodes"]) == 3
    assert len(result["edges"]) == 2
    assert callback.terminal_sent
    assert callback.complete_result is not None
    assert callback.complete_result.status == "completed"
    assert 100 in callback.progress_calls


@pytest.mark.asyncio
async def test_generate_mindmap_no_usable_seed_rejected():
    payload = _build_request_payload(topic=None, ideas=None)
    callback = FakeCallbackClient()
    ctx = {
        "llm": FakeLLMProvider([]),
        "decision_model": FakeDecisionModel(),
        "embedding_provider": FakeEmbeddingProvider(),
        "vector_store": FakeVectorStore(),
        "tools": {},
        "settings": Settings(),
        "callback": callback,
    }

    result = await generate_mindmap(ctx, payload)

    assert result["status"] == "rejected"
    assert callback.terminal_sent
    assert callback.fail_result is not None
    assert callback.fail_result.status == "rejected"
    assert callback.fail_result.error.stage == "intake"
    assert callback.fail_result.error.retryable is False


@pytest.mark.asyncio
async def test_generate_mindmap_unhandled_crash_marks_failed():
    payload = _build_request_payload()
    callback = FakeCallbackClient()
    ctx = {
        "llm": ExplodingLLM([]),
        "decision_model": FakeDecisionModel(),
        "embedding_provider": FakeEmbeddingProvider(),
        "vector_store": FakeVectorStore(),
        "tools": {},
        "settings": Settings(),
        "callback": callback,
    }

    result = await generate_mindmap(ctx, payload)

    assert result["status"] == "failed"
    assert callback.terminal_sent
    assert callback.fail_result is not None
    assert callback.fail_result.status == "failed"
    assert callback.fail_result.error.retryable is True
    assert "Simulated unhandled LLM crash" in callback.fail_result.error.message


@pytest.mark.asyncio
async def test_generate_mindmap_regenerate_full_success():
    map_id = uuid.uuid4()
    req = GenerationRequest(
        job_id=uuid.uuid4(),
        map_id=map_id,
        job_type=JobType.REGENERATE_FULL,
        regeneration_instruction="Focus more on cellular respiration connections",
        callback_base_url="https://api.test",
        callback_token="test-token",
    )
    payload = req.model_dump(mode="json")

    vector_store = FakeVectorStore()
    await vector_store.upsert_chunks(
        namespace=str(map_id),
        chunks=[
            VectorChunk(
                id="chunk-1",
                chunk_text="Photosynthesis converts solar energy to chemical energy.",
                embedding=[0.1] * 1024,
                source=ChunkSource.UPLOADED_SEED,
            )
        ],
    )

    callback = FakeCallbackClient()
    ctx = {
        "llm": FakeLLMProvider([json.dumps(VALID_DRAFT_PAYLOAD)]),
        "decision_model": FakeDecisionModel(),
        "embedding_provider": FakeEmbeddingProvider(),
        "vector_store": vector_store,
        "tools": {},
        "settings": Settings(),
        "callback": callback,
    }

    result = await generate_mindmap(ctx, payload)

    assert result["status"] == "completed"
    assert callback.terminal_sent
    assert callback.complete_result is not None


@pytest.mark.asyncio
async def test_generate_mindmap_regenerate_full_no_seed_rejected():
    map_id = uuid.uuid4()
    req = GenerationRequest(
        job_id=uuid.uuid4(),
        map_id=map_id,
        job_type=JobType.REGENERATE_FULL,
        regeneration_instruction="Focus on connections",
        callback_base_url="https://api.test",
        callback_token="test-token",
    )
    payload = req.model_dump(mode="json")

    vector_store = FakeVectorStore()  # empty namespace!
    callback = FakeCallbackClient()
    ctx = {
        "llm": FakeLLMProvider([]),
        "decision_model": FakeDecisionModel(),
        "embedding_provider": FakeEmbeddingProvider(),
        "vector_store": vector_store,
        "tools": {},
        "settings": Settings(),
        "callback": callback,
    }

    result = await generate_mindmap(ctx, payload)

    assert result["status"] == "rejected"
    assert callback.terminal_sent
    assert callback.fail_result is not None
    assert callback.fail_result.status == "rejected"
    assert "No original seed chunks found" in callback.fail_result.error.message
