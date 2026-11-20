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
import sys
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

_pkg_root = Path(__file__).resolve().parent.parent
if str(_pkg_root) not in sys.path:
    sys.path.insert(0, str(_pkg_root))

# Load .env from llm folder and repo root
load_dotenv(_pkg_root.parent / ".env")
load_dotenv(_pkg_root.parent.parent / ".env")
load_dotenv(find_dotenv())

from arq.connections import RedisSettings
from config import get_settings
from embeddings.registry import get_embedding_provider
from providers.registry import get_decision_model, get_llm_provider
from rag.pinecone_store import PineconeVectorStore
from tools.registry import build_tool_registry
from tracing import configure_langsmith
from worker.tasks import chat_turn_task, generate_mindmap, regenerate_node_task

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
    configure_langsmith(settings.langsmith)
    logging.info(
        "Worker started: llm=%s decision_model=%s embeddings=%s",
        llm.name,
        ctx["decision_model"].name,
        ctx["embedding_provider"].model_name,
    )


async def on_shutdown(ctx: dict) -> None:
    pass  # httpx clients inside providers are per-call; nothing persistent to close here today


def build_worker_redis_settings() -> RedisSettings:
    """Build resilient RedisSettings accommodating cloud TLS endpoints (e.g. Upstash)."""
    cfg = get_settings().arq
    rs = RedisSettings.from_dsn(cfg.redis_url)
    return RedisSettings(
        host=rs.host,
        port=rs.port,
        database=rs.database,
        username=rs.username,
        password=rs.password,
        ssl=rs.ssl,
        ssl_keyfile=rs.ssl_keyfile,
        ssl_certfile=rs.ssl_certfile,
        ssl_cert_reqs=rs.ssl_cert_reqs,
        ssl_ca_certs=rs.ssl_ca_certs,
        ssl_ca_data=rs.ssl_ca_data,
        ssl_check_hostname=rs.ssl_check_hostname,
        conn_timeout=getattr(cfg, "conn_timeout", 15),
        conn_retries=getattr(cfg, "conn_retries", 10),
        conn_retry_delay=2,
        retry_on_timeout=True,
    )


class WorkerSettings:
    functions = [generate_mindmap, regenerate_node_task, chat_turn_task]
    on_startup = on_startup
    on_shutdown = on_shutdown
    redis_settings = build_worker_redis_settings()
    max_jobs = get_settings().arq.max_jobs
    job_timeout = get_settings().arq.job_timeout_s
