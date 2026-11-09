from __future__ import annotations

import json
import uuid

import pytest
from config import GenerationConfig, Settings
from contracts.models import ChunkSource, GenerationRequest, JobType
from rag.pinecone_store import VectorChunk
from worker.tasks import generate_mindmap

from tests.conftest import (
    VALID_DRAFT_PAYLOAD,
    FakeCallbackClient,
    FakeDecisionModel,
    FakeEmbeddingProvider,
    FakeLLMProvider,
    FakeVectorStore,
)


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


class CapturingLLM(FakeLLMProvider):
    def __init__(self, responses: list[str]):
        super().__init__(responses)
        self.systems: list[str] = []

    async def generate(self, messages, *, system=None, **kwargs):
        self.systems.append(system or "")
        return await super().generate(messages, system=system, **kwargs)


@pytest.mark.asyncio
async def test_feature_flag_dynamic_prompts_enabled():
    payload = _build_request_payload(topic="Short topic", ideas=None)
    callback = FakeCallbackClient()
    llm = CapturingLLM([json.dumps(VALID_DRAFT_PAYLOAD)])
    settings = Settings(generation=GenerationConfig(enable_dynamic_prompts=True))
    ctx = {
        "llm": llm,
        "decision_model": FakeDecisionModel(),
        "embedding_provider": FakeEmbeddingProvider(),
        "vector_store": FakeVectorStore(),
        "tools": {},
        "settings": settings,
        "callback": callback,
    }

    result = await generate_mindmap(ctx, payload)
    assert result["status"] == "completed"
    assert len(llm.systems) == 1
    # Dynamic prompt has min_nodes to max_nodes and mode rule
    assert "Mode TOPIC" in llm.systems[0] or "Mode DOCUMENT" in llm.systems[0]


@pytest.mark.asyncio
async def test_feature_flag_dynamic_prompts_disabled_falls_back():
    payload = _build_request_payload(topic="Short topic", ideas=None)
    callback = FakeCallbackClient()
    llm = CapturingLLM([json.dumps(VALID_DRAFT_PAYLOAD)])
    settings = Settings(generation=GenerationConfig(enable_dynamic_prompts=False))
    ctx = {
        "llm": llm,
        "decision_model": FakeDecisionModel(),
        "embedding_provider": FakeEmbeddingProvider(),
        "vector_store": FakeVectorStore(),
        "tools": {},
        "settings": settings,
        "callback": callback,
    }

    result = await generate_mindmap(ctx, payload)
    assert result["status"] == "completed"
    assert len(llm.systems) == 1
    # Fallback uses static GENERATION_SYSTEM_PROMPT
    from orchestration.prompts import GENERATION_SYSTEM_PROMPT

    assert llm.systems[0] == GENERATION_SYSTEM_PROMPT
