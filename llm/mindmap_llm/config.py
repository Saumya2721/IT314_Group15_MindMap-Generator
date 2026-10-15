"""
Central configuration, read entirely from environment variables.

CHANGED in the worker/Pinecone restructure: DatabaseConfig is gone — this
process makes no Postgres connections at all. In its place: PineconeConfig
(vector storage) and ArqConfig (the queue this worker listens on).
Callback auth is NOT global config — it travels per-job inside each
GenerationRequest (callback_base_url/callback_token), minted by the
backend, so a compromised worker process can't use a static secret to
call back about jobs it was never given.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

# Load .env file from llm directory, project root, or backend directory
_llm_dir = Path(__file__).resolve().parent
_project_dir = _llm_dir.parent
for _env_path in (_llm_dir / ".env", _project_dir / ".env", _project_dir / "backend" / ".env"):
    if _env_path.exists():
        load_dotenv(_env_path)
load_dotenv(find_dotenv())


def _get_bool(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


def _get_int(name: str, default: int) -> int:
    val = os.environ.get(name)
    return int(val) if val is not None else default


def _get_float(name: str, default: float) -> float:
    val = os.environ.get(name)
    return float(val) if val is not None else default


@dataclass(frozen=True)
class ArqConfig:
    redis_url: str = field(
        default_factory=lambda: os.environ.get("REDIS_URL", "redis://localhost:6379")
    )
    max_jobs: int = field(default_factory=lambda: _get_int("ARQ_MAX_JOBS", 4))
    # Must exceed the slowest realistic generation (detailed mode budget
    # is 180s) with real headroom for retries inside the graph.
    job_timeout_s: int = field(default_factory=lambda: _get_int("ARQ_JOB_TIMEOUT_S", 300))


@dataclass(frozen=True)
class LLMConfig:
    """
    Generic primary-LLM config. LLM_PROVIDER selects which concrete
    providers.base.LLMProvider implementation is instantiated by
    providers.registry.get_llm_provider(). Add new providers by
    registering them there — nothing else needs to change.
    """

    provider: str = field(default_factory=lambda: os.environ.get("LLM_PROVIDER", "groq"))
    model: str = field(
        default_factory=lambda: os.environ.get("LLM_MODEL", "llama-3.3-70b-versatile")
    )
    api_key: str = field(default_factory=lambda: os.environ.get("LLM_API_KEY", ""))
    api_base: str | None = field(default_factory=lambda: os.environ.get("LLM_API_BASE") or None)
    max_output_tokens: int = field(default_factory=lambda: _get_int("LLM_MAX_OUTPUT_TOKENS", 8000))
    request_timeout_s: float = field(default_factory=lambda: _get_float("LLM_TIMEOUT_S", 90.0))


@dataclass(frozen=True)
class DecisionModelConfig:
    """
    Config for the "System One" fast-decision model (e.g. Jev / TypeSafe).
    Used only for classify / route / score / branch calls — never for
    generating map content. If not configured (or a call fails), every
    call site falls back to a cheap heuristic or the primary LLM so the
    pipeline still works without it (NFR-07: graceful degradation).
    """

    enabled: bool = field(default_factory=lambda: _get_bool("DECISION_MODEL_ENABLED", True))
    provider: str = field(default_factory=lambda: os.environ.get("DECISION_MODEL_PROVIDER", "jev"))
    api_key: str = field(default_factory=lambda: os.environ.get("TYPESAFE_API_KEY", ""))
    api_base: str = field(
        default_factory=lambda: os.environ.get("TYPESAFE_API_BASE", "https://api.typesafe.ai/v1")
    )
    request_timeout_s: float = field(
        default_factory=lambda: _get_float("DECISION_MODEL_TIMEOUT_S", 3.0)
    )


@dataclass(frozen=True)
class EmbeddingConfig:
    """
    Generic embedding config. `dimension` must match BOTH the model's
    real output size and the Pinecone index's configured dimension
    (see scripts/create_pinecone_index.py) — there is no more
    auto-projection safety net (see embeddings/huggingface_provider.py).
    """

    provider: str = field(
        default_factory=lambda: os.environ.get("EMBEDDING_PROVIDER", "huggingface")
    )
    model: str = field(
        default_factory=lambda: os.environ.get(
            "EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
        )
    )
    api_key: str = field(default_factory=lambda: os.environ.get("EMBEDDING_API_KEY", ""))
    api_base: str = field(
        default_factory=lambda: os.environ.get(
            "EMBEDDING_API_BASE", "https://router.huggingface.co/hf-inference/models"
        )
    )
    dimension: int = field(default_factory=lambda: _get_int("EMBEDDING_DIMENSION", 384))
    batch_size: int = field(default_factory=lambda: _get_int("EMBEDDING_BATCH_SIZE", 32))


@dataclass(frozen=True)
class PineconeConfig:
    api_key: str = field(default_factory=lambda: os.environ.get("PINECONE_API_KEY", ""))
    index_name: str = field(
        default_factory=lambda: os.environ.get("PINECONE_INDEX", "mindmap-chunks")
    )
    # Convex-combination weight for hybrid dense+sparse queries
    # (1.0 = dense-only, 0.0 = sparse-only).
    dense_weight: float = field(default_factory=lambda: _get_float("PINECONE_DENSE_WEIGHT", 0.5))


@dataclass(frozen=True)
class ToolConfig:
    web_search_provider: str = field(
        default_factory=lambda: os.environ.get("SEARCH_PROVIDER", "tavily")
    )
    web_search_api_key: str = field(default_factory=lambda: os.environ.get("SEARCH_API_KEY", ""))
    arxiv_api_base: str = field(
        default_factory=lambda: os.environ.get(
            "ARXIV_API_BASE", "http://export.arxiv.org/api/query"
        )
    )
    tool_timeout_s: float = field(default_factory=lambda: _get_float("TOOL_TIMEOUT_S", 15.0))


@dataclass(frozen=True)
class GenerationConfig:
    max_generation_retries: int = field(
        default_factory=lambda: _get_int("MAX_GENERATION_RETRIES", 2)
    )
    default_detail_level: str = field(
        default_factory=lambda: os.environ.get(
            "DEFAULT_DETAIL_LEVEL",
            "",  # empty => Jev/heuristic infers it
        )
    )
    rag_top_k: int = field(default_factory=lambda: _get_int("RAG_TOP_K", 12))
    rag_rerank_top_k: int = field(default_factory=lambda: _get_int("RAG_RERANK_TOP_K", 6))
    overview_latency_budget_s: float = field(
        default_factory=lambda: _get_float("OVERVIEW_LATENCY_BUDGET_S", 30.0)
    )
    detailed_latency_budget_s: float = field(
        default_factory=lambda: _get_float("DETAILED_LATENCY_BUDGET_S", 180.0)
    )


@dataclass(frozen=True)
class Settings:
    arq: ArqConfig = field(default_factory=ArqConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    decision_model: DecisionModelConfig = field(default_factory=DecisionModelConfig)
    embedding: EmbeddingConfig = field(default_factory=EmbeddingConfig)
    pinecone: PineconeConfig = field(default_factory=PineconeConfig)
    tools: ToolConfig = field(default_factory=ToolConfig)
    generation: GenerationConfig = field(default_factory=GenerationConfig)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide singleton. Re-read by clearing the cache in tests if needed."""
    return Settings()
