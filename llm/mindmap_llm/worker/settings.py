"""
ARQ worker entry point. Run with:

    arq worker.settings.WorkerSettings

Shared, expensive-to-build clients (LLM, decision model, embeddings,
Pinecone, tools) are built ONCE per worker process in on_startup and
stashed in `ctx`, not rebuilt per job — this is the ARQ-recommended
pattern and avoids re-establishing HTTP connections for every task.
"""

from __future__ import annotations

import logging

from arq.connections import RedisSettings

from config import get_settings
from embeddings.registry import get_embedding_provider
from providers.registry import get_decision_model, get_llm_provider
from rag.pinecone_store import PineconeVectorStore
from tools.registry import build_tool_registry
from worker.tasks import generate_mindmap

logging.basicConfig(level=logging.INFO)


async def on_startup(ctx: dict) -> None:
    settings = get_settings()
    llm = get_llm_provider(settings)
    ctx["settings"] = settings
    ctx["llm"] = llm
    ctx["decision_model"] = get_decision_model(settings, llm_fallback=llm)
    ctx["embedding_provider"] = get_embedding_provider(settings)
    ctx["vector_store"] = PineconeVectorStore(
        api_key=settings.pinecone.api_key,
        index_name=settings.pinecone.index_name,
        dense_weight=settings.pinecone.dense_weight,
    )
    ctx["tools"] = build_tool_registry(settings)
    logging.info(
        "Worker started: llm=%s decision_model=%s embeddings=%s",
        llm.name, ctx["decision_model"].name, ctx["embedding_provider"].model_name,
    )


async def on_shutdown(ctx: dict) -> None:
    pass  # httpx clients inside providers are per-call; nothing persistent to close here today


class WorkerSettings:
    functions = [generate_mindmap]
    on_startup = on_startup
    on_shutdown = on_shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().arq.redis_url)
    max_jobs = get_settings().arq.max_jobs
    job_timeout = get_settings().arq.job_timeout_s
